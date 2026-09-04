##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""MongoDB document provider, driver, and administration tests."""

from __future__ import annotations

import json
import sys
import unittest
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

from pgadmin.cdeadmin.providers.mongodb.client import (  # noqa: E402
    MongoDBClient,
    MongoDBClientError,
)
from pgadmin.cdeadmin.providers.mongodb.provider import (  # noqa: E402
    MongoDBPilotProvider,
    PROFILE,
)


class SecretLease:
    def __init__(self, value):
        self.value = bytearray(value)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        for offset in range(len(self.value)):
            self.value[offset] = 0
        self.closed = True

    def use(self, callback):
        return callback(memoryview(self.value))


class Cursor:
    def __init__(self, documents):
        self.documents = list(documents)
        self.index = 0

    def sort(self, _sort):
        return self

    def skip(self, count):
        self.documents = self.documents[count:]
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def batch_size(self, _count):
        return self

    def close(self):
        return None

    def try_next(self):
        if not self.documents:
            return None
        return self.documents.pop(0)

    def __iter__(self):
        return self

    def __next__(self):
        if self.index >= len(self.documents):
            raise StopIteration
        value = self.documents[self.index]
        self.index += 1
        return value


class Collection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])

    def find(self, *_args, **_kwargs):
        return Cursor(self.documents)

    def aggregate(self, _pipeline, **_kwargs):
        return Cursor(self.documents)

    def watch(self, **_kwargs):
        value = Cursor(self.documents)
        value.resume_token = {'token': 1}
        return value

    def count_documents(self, _selector, **_kwargs):
        return len(self.documents)

    def insert_many(self, documents, **_kwargs):
        self.documents.extend(documents)
        return SimpleNamespace(acknowledged=True)

    @staticmethod
    def list_indexes():
        return [{'name': '_id_', 'key': {'_id': 1}, 'v': 2}]


class Database:
    def __init__(self, name, client):
        self.name = name
        self.client = client
        self.collections = {
            'widgets': Collection([{'_id': 1, 'name': 'first'}]),
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())

    def command(self, command, **_kwargs):
        name = command if isinstance(command, str) else next(iter(command))
        if name == 'ping':
            return {'ok': 1.0}
        if name == 'buildInfo':
            return {
                'version': '8.2.6',
                'gitVersion': '5d25c835745d06f712320b6cdae9d50b7b43663e',
            }
        if name == 'hello':
            return {
                'setName': 'cdeadmin-rs', 'maxWireVersion': 27,
                'minWireVersion': 0,
            }
        if name == 'connectionStatus':
            return {'authInfo': {
                'authenticatedUsers': [{'user': 'operator', 'db': 'admin'}],
                'authenticatedUserRoles': [],
                'authenticatedUserPrivileges': [],
            }}
        if name == 'usersInfo':
            return {'users': []}
        if name == 'rolesInfo':
            return {'roles': []}
        return {'ok': 1.0, 'command': name}

    @staticmethod
    def list_collections():
        return [{
            'name': 'widgets', 'type': 'collection',
            'options': {'validator': {'name': {'$type': 'string'}}},
        }]


class DriverSession:
    in_transaction = False
    has_ended = False
    session_id = {'id': b'opaque-session'}

    def end_session(self):
        self.has_ended = True

    def start_transaction(self):
        self.in_transaction = True

    def commit_transaction(self):
        self.in_transaction = False

    def abort_transaction(self):
        self.in_transaction = False


class DriverClient:
    def __init__(self, **arguments):
        self.arguments = arguments
        self.databases = {}
        self.admin = self['admin']
        self.closed = False

    def __getitem__(self, name):
        return self.databases.setdefault(name, Database(name, self))

    def start_session(self, **options):
        self.session_options = options
        return DriverSession()

    @staticmethod
    def list_database_names():
        return ['admin', 'qualification']

    def close(self):
        self.closed = True


class Permissions:
    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True


def client(connector=DriverClient, secret_acquirer=None):
    module = SimpleNamespace(MongoClient=connector)
    return MongoDBClient(secret_acquirer=secret_acquirer, module=module)


def context():
    return SimpleNamespace(
        endpoint_id='endpoint', mode='legacy_native',
        runtime_verification_state='verified',
        verified_runtime_family='mongodb',
        declared_runtime_family='mongodb',
        effective_permissions=frozenset({
            'data_read', 'data_write', 'administer', 'execute', 'network',
        }),
        session_namespace='session', cache_namespace='cache',
    )


class MongoDBProviderTests(unittest.TestCase):

    def test_compatibility_matrix_keeps_exact_claims_fail_closed(self):
        matrix = json.loads((
            WEB / 'pgadmin/cdeadmin/providers/mongodb/'
            'compatibility_matrix.json'
        ).read_text(encoding='utf-8'))
        self.assertEqual('8.2.6', matrix['reference_profile'][
            'server_version'
        ])
        self.assertEqual('4.17.0', matrix['reference_profile'][
            'driver_version'
        ])
        self.assertEqual(
            ['8.2.6'], matrix['patch_policy']['qualified_versions']
        )
        platforms = {
            item['platform']: item for item in matrix['client_platforms']
        }
        self.assertEqual({'linux', 'macos', 'windows'}, set(platforms))
        self.assertEqual('not_run', platforms['windows']['live_suite'])
        self.assertEqual('not_run', platforms['macos']['live_suite'])

    def test_profile_selects_document_language_and_production_renderer(self):
        self.assertEqual('application/json', PROFILE.language_mime_type)
        self.assertEqual('document', PROFILE.result_renderer_kind)
        self.assertEqual(
            'cdeadmin.result.document.tree', PROFILE.result_renderer_id
        )
        self.assertEqual('documents', PROFILE.result_records_field)
        self.assertEqual(26, len(PROFILE.resource_kinds))

    def test_exact_runtime_and_structured_route_are_driver_owned(self):
        created = []

        def connector(**arguments):
            value = DriverClient(**arguments)
            created.append(value)
            return value

        value = client(connector)
        identity = value.runtime_identity({'route': {
            'host': '127.0.0.1', 'port': 27017,
            'database': 'qualification', 'route_id': 'one',
            'user': 'operator', 'connection_timeout': 9,
        }})
        self.assertEqual('8.2.6', identity['version'])
        self.assertEqual(27, identity['native']['max_wire_version'])
        self.assertEqual('127.0.0.1', created[0].arguments['host'])
        self.assertEqual('operator', created[0].arguments['username'])
        self.assertEqual('qualification', created[0].arguments['authSource'])
        self.assertEqual(9000, created[0].arguments['connectTimeoutMS'])
        self.assertNotIn('route_id', created[0].arguments)
        self.assertTrue(created[0].closed)

        for route in (
            {'uri': 'mongodb://user:password@example.invalid'},
            {'host': 'localhost', 'password': 'inline'},
        ):
            with self.assertRaisesRegex(
                MongoDBClientError, 'unknown fields'
            ):
                value.runtime_identity({'route': route})

    def test_secret_is_leased_only_for_connector_and_failures_redact_it(self):
        observed = {}
        leases = []

        def acquire(reference, principal, purpose, expected_kind):
            observed['acquisition'] = (
                reference, principal, purpose, expected_kind
            )
            lease = SecretLease(b'mongodb-password-canary')
            leases.append(lease)
            return lease

        def connector(**arguments):
            observed['password'] = arguments.pop('password')
            observed['keys'] = frozenset(arguments)
            return DriverClient(**arguments)

        value = client(connector, acquire)
        handle = value.open_session({'route': {
            'host': 'localhost', 'username': 'operator',
            'auth_source': 'admin',
            'credential_reference_id': 'secret-one',
            'principal_reference': 'principal-one',
        }})
        self.assertEqual('mongodb-password-canary', observed['password'])
        self.assertEqual(
            ('secret-one', 'principal-one', 'connect',
             'database_password'),
            observed['acquisition'],
        )
        self.assertTrue(leases[0].closed)
        self.assertEqual({0}, set(leases[0].value))
        self.assertNotIn('credential_reference_id', observed['keys'])
        value.close()
        self.assertTrue(handle.closed)

    def test_topology_consistency_pool_tls_and_compression_are_forwarded(self):
        created = []

        def connector(**arguments):
            value = DriverClient(**arguments)
            created.append(value)
            return value

        value = client(connector)
        identity = value.runtime_identity({'route': {
            'host': 'mongo-a', 'port': 27017,
            'contact_points': 'mongo-b:27018,mongo-c:27019',
            'auth_mechanism': 'NONE', 'tls': True,
            'tls_ca_file': '/certs/ca.pem',
            'tls_allow_invalid_hostnames': False,
            'compressors': 'zstd,zlib', 'zlib_compression_level': 6,
            'read_preference': 'nearest',
            'read_concern_level': 'majority', 'write_concern': '2',
            'retry_reads': True, 'retry_writes': False,
            'min_pool_size': 2, 'max_pool_size': 40,
            'max_connecting': 4, 'max_idle_time_ms': 60000,
            'wait_queue_timeout_ms': 3000,
            'heartbeat_frequency_ms': 2000,
            'wait_queue_multiple': 6,
            'server_monitoring_mode': 'poll',
            'tls_disable_ocsp_endpoint_check': True,
            'enable_overload_retargeting': True,
            'max_adaptive_retries': 4,
            'server_api_version': '1',
            'server_api_strict': True,
            'auth_oidc_allowed_hosts': ['login.example.test'],
            'tz_aware': False,
            'uuid_representation': 'pythonLegacy',
            'unicode_decode_error_handler': 'replace',
            'fsync': True,
        }})

        self.assertEqual('8.2.6', identity['version'])
        arguments = created[0].arguments
        self.assertEqual(
            ['mongo-a', 'mongo-b:27018', 'mongo-c:27019'],
            arguments['host'],
        )
        self.assertNotIn('port', arguments)
        self.assertNotIn('username', arguments)
        self.assertEqual('zstd,zlib', arguments['compressors'])
        self.assertEqual('nearest', arguments['readPreference'])
        self.assertEqual('majority', arguments['readConcernLevel'])
        self.assertEqual(2, arguments['w'])
        self.assertEqual(40, arguments['maxPoolSize'])
        self.assertEqual(2000, arguments['heartbeatFrequencyMS'])
        self.assertEqual(6, arguments['waitQueueMultiple'])
        self.assertEqual('poll', arguments['serverMonitoringMode'])
        self.assertTrue(arguments['tlsDisableOCSPEndpointCheck'])
        self.assertTrue(arguments['enableOverloadRetargeting'])
        self.assertEqual(4, arguments['maxAdaptiveRetries'])
        self.assertEqual('1', arguments['server_api'].version)
        self.assertTrue(arguments['server_api'].strict)
        self.assertEqual(
            ['login.example.test'], arguments['authOIDCAllowedHosts']
        )
        self.assertFalse(arguments['tz_aware'])
        self.assertEqual('pythonLegacy', arguments['uuidRepresentation'])
        self.assertEqual(
            'replace', arguments['unicode_decode_error_handler']
        )
        self.assertTrue(arguments['fsync'])

    def test_session_and_transaction_defaults_are_driver_owned(self):
        created = []

        def connector(**arguments):
            value = DriverClient(**arguments)
            created.append(value)
            return value

        value = client(connector)
        handle = value.open_session({'route': {
            'host': 'mongo-a', 'port': 27017,
            'auth_mechanism': 'NONE',
            'session_causal_consistency': False,
            'session_snapshot': True,
            'transaction_read_concern': 'snapshot',
            'transaction_write_concern': 'majority',
            'write_concern_timeout_ms': 2000,
            'journal': True,
            'transaction_max_commit_time_ms': 5000,
        }})
        options = created[0].session_options
        self.assertFalse(options['causal_consistency'])
        self.assertTrue(options['snapshot'])
        transaction = options['default_transaction_options']
        self.assertEqual('snapshot', transaction.read_concern.level)
        self.assertEqual('majority', transaction.write_concern.document['w'])
        self.assertEqual(5000, transaction.max_commit_time_ms)
        handle.close()

    def test_snapshot_session_rejects_causal_consistency(self):
        value = client(lambda **arguments: DriverClient(**arguments))
        with self.assertRaisesRegex(
            MongoDBClientError, 'mutually exclusive'
        ):
            value.open_session({'route': {
                'host': 'mongo-a', 'port': 27017,
                'auth_mechanism': 'NONE',
                'session_causal_consistency': True,
                'session_snapshot': True,
            }})

    def test_invalid_compression_and_topology_conflicts_fail_closed(self):
        value = client()
        with self.assertRaisesRegex(
            MongoDBClientError, 'compressor selection'
        ):
            value.runtime_identity({'route': {
                'host': 'localhost', 'compressors': 'unsupported',
            }})
        with self.assertRaisesRegex(
            MongoDBClientError, 'load-balanced mode conflicts'
        ):
            value.runtime_identity({'route': {
                'host': 'localhost', 'load_balanced': True,
                'replica_set': 'rs0',
            }})
        with self.assertRaisesRegex(
            MongoDBClientError, 'minimum pool size exceeds'
        ):
            value.runtime_identity({'route': {
                'host': 'localhost', 'min_pool_size': 10,
                'max_pool_size': 2,
            }})
        with self.assertRaisesRegex(
            MongoDBClientError, 'server monitoring mode'
        ):
            value.runtime_identity({'route': {
                'host': 'localhost', 'server_monitoring_mode': 'unsafe',
            }})

    def test_aws_and_oidc_use_typed_leased_credentials(self):
        values = {
            'aws-secret': b'aws-secret-canary',
            'aws-session': b'aws-session-canary',
            'oidc-token': b'oidc-token-canary',
        }
        acquisitions = []
        created = []

        def acquire(reference, principal, purpose, expected_kind):
            acquisitions.append((
                reference, principal, purpose, expected_kind
            ))
            return SecretLease(values[reference])

        def connector(**arguments):
            value = DriverClient(**arguments)
            created.append(value)
            return value

        value = client(connector, acquire)
        value.runtime_identity({'route': {
            'host': 'localhost', 'username': 'access-key-id',
            'auth_mechanism': 'MONGODB-AWS',
            'credential_reference_id': 'aws-secret',
            'credential_kind': 'cloud_secret_access_key',
            'credential_references': {
                'cloud_secret_access_key': 'aws-secret',
                'cloud_session_token': 'aws-session',
            },
            'principal_reference': 'principal-one',
        }})
        self.assertEqual(
            'aws-secret-canary', created[0].arguments['password']
        )
        self.assertEqual(
            'aws-session-canary',
            created[0].arguments['authMechanismProperties'][
                'AWS_SESSION_TOKEN'
            ],
        )

        value.runtime_identity({'route': {
            'host': 'localhost', 'username': 'oidc-user',
            'auth_mechanism': 'MONGODB-OIDC',
            'oidc_environment': 'callback',
            'credential_reference_id': 'oidc-token',
            'credential_kind': 'oidc_access_token',
            'credential_references': {
                'oidc_access_token': 'oidc-token',
            },
            'principal_reference': 'principal-one',
        }})
        callback = created[1].arguments['authMechanismProperties'][
            'OIDC_MACHINE_CALLBACK'
        ]
        token = callback.fetch(None)
        self.assertEqual('oidc-token-canary', token.access_token)
        self.assertIn(
            ('oidc-token', 'principal-one', 'connect', 'oidc_access_token'),
            acquisitions,
        )

    def test_json_query_results_preserve_extended_json_and_are_bounded(self):
        value = client()
        handle = value.open_session({'route': {
            'host': 'localhost', 'database': 'qualification',
        }})
        token = value.execute(handle, {'source': json.dumps({
            'operation': 'find', 'database': 'qualification',
            'collection': 'widgets', 'filter': {}, 'limit': 10,
        })})
        result = value.describe_result(token)
        self.assertEqual('document', result['result_kind'])
        self.assertEqual(
            {'$numberInt': '1'},
            result['payload']['documents'][0]['_id'],
        )
        self.assertFalse(value.cancel(token))
        transaction = value.describe_transaction(handle)
        self.assertTrue(transaction['driver_observation_only'])
        self.assertFalse(
            transaction['finality_interpreted_by_common_code']
        )
        value.control_transaction(handle, 'begin')
        self.assertTrue(value.describe_transaction(handle)['in_transaction'])
        value.control_transaction(handle, 'rollback')
        self.assertFalse(value.describe_transaction(handle)['in_transaction'])
        with self.assertRaisesRegex(MongoDBClientError, 'read-only'):
            value.execute(handle, {'source': json.dumps({
                'operation': 'command', 'database': 'qualification',
                'command': {'dropDatabase': 1},
            })})
        value.close()

    def test_resource_discovery_includes_document_native_objects(self):
        value = client()
        resources = value.list_resources({'route': {
            'host': 'localhost', 'database': 'qualification',
        }})
        kinds = {item['resource_kind'] for item in resources}
        self.assertTrue({
            'deployment', 'replica-set', 'database', 'collection',
            'validator', 'index', 'change-stream', 'aggregation-pipeline',
        }.issubset(kinds))
        collection = next(
            item for item in resources
            if item['resource_kind'] == 'collection' and
            item['native']['database'] == 'qualification'
        )
        self.assertEqual(
            'qualification', collection['native']['database']
        )

    def test_visual_catalog_and_plan_are_provider_owned_and_redacted(self):
        value = client()
        provider = MongoDBPilotProvider(context(), Permissions(), value)
        descriptor = provider.visual_admin_descriptor()
        coverage = descriptor['concept_coverage']
        self.assertTrue(coverage['declaration_ready'])
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        document = next(
            item for item in descriptor['objects']
            if item['resource_kind'] == 'document'
        )
        insert = next(
            item for item in document['operations']
            if item['operation_id'] == 'insert'
        )
        self.assertTrue(insert['target_required'])
        self.assertEqual(['collection'], insert['target_resource_kinds'])
        self.assertTrue(insert['native_supported'])
        self.assertTrue(next(
            operation for item in descriptor['objects']
            if item['resource_kind'] == 'replica-set'
            for operation in item['operations']
            if operation['operation_id'] == 'alter'
        )['native_supported'])

        target = {
            'resource_kind': 'collection',
            'extensions': {'mongodb': {'native': {
                'database': 'qualification', 'collection': 'widgets',
            }}},
        }
        plan = provider.plan_visual_admin({
            'resource_kind': 'document', 'operation_id': 'insert',
            'target_resource': target,
            'draft': {'values': {'name': 'planned'}, 'options': {}},
            '_provider_route': {
                'host': 'localhost', 'database': 'qualification',
            },
        })
        self.assertEqual('ready', plan['state'])
        self.assertNotIn('_provider_route', str(plan))
        self.assertEqual(
            'pymongo', plan['command_preview']['driver']
        )

    def test_aggregation_workspace_is_typed_bounded_and_read_only(self):
        provider = MongoDBPilotProvider(
            context(), Permissions(), client()
        )
        descriptor = provider.visual_admin_descriptor()
        workspace = next(
            item for item in descriptor['objects']
            if item['resource_kind'] == 'aggregation-pipeline'
        )
        execute = next(
            item for item in workspace['operations']
            if item['operation_id'] == 'execute'
        )
        self.assertEqual('read', execute['mutation_class'])
        self.assertFalse(execute['confirmation_required'])
        self.assertEqual(
            'mongodb-aggregation-pipeline', execute['form']['form_id']
        )
        target = {
            'resource_kind': 'aggregation-pipeline',
            'extensions': {'mongodb': {'native': {
                'database': 'qualification', 'collection': 'widgets',
            }}},
        }
        plan = provider.plan_visual_admin({
            'resource_kind': 'aggregation-pipeline',
            'operation_id': 'execute', 'target_resource': target,
            'draft': {
                'pipeline': [{'$match': {'name': 'first'}}],
                'options': {}, 'max_documents': 1,
            },
            '_provider_route': {
                'host': 'localhost', 'database': 'qualification',
            },
        })
        result = provider.apply_visual_admin({
            'plan_id': plan['plan_id'],
            'plan_digest': plan['plan_digest'], 'confirmed': False,
        })['provider_result']['observation']
        self.assertEqual(1, result['document_count'])
        self.assertFalse(result['truncated'])
        blocked = provider.validate_visual_admin({
            'resource_kind': 'aggregation-pipeline',
            'operation_id': 'execute', 'target_resource': target,
            'draft': {
                'pipeline': [{'$merge': 'other'}], 'options': {},
            },
            '_provider_route': {
                'host': 'localhost', 'database': 'qualification',
            },
        })
        self.assertFalse(blocked['valid'])

    def test_advanced_route_streaming_and_change_stream_cancellation(self):
        created = []

        def connector(**arguments):
            value = DriverClient(**arguments)
            value['qualification'].collections['widgets'] = Collection([
                {'_id': 1, 'optional': 'one'}, {'_id': 2}, {'_id': 3},
            ])
            created.append(value)
            return value

        value = client(connector)
        handle = value.open_session({'route': {
            'host': 'localhost', 'database': 'qualification',
            'auth_source': 'admin', 'replica_set': 'cdeadmin-rs',
            'direct_connection': False, 'tls': True,
            'tls_ca_file': '/tmp/ca.pem',
            'tls_certificate_key_file': '/tmp/client.pem',
            'connect_timeout_ms': 7000,
            'server_selection_timeout_ms': 8000,
            'socket_timeout_ms': 9000,
        }})
        self.assertEqual('admin', created[0].arguments['authSource'])
        self.assertEqual('cdeadmin-rs', created[0].arguments['replicaSet'])
        self.assertTrue(created[0].arguments['tls'])
        token = value.execute(handle, {'source': json.dumps({
            'operation': 'find', 'database': 'qualification',
            'collection': 'widgets', 'batch_size': 2,
            'max_documents': 3,
        })})
        first = value.describe_result(token)
        self.assertEqual(2, len(first['payload']['documents']))
        self.assertFalse(first['complete'])
        second = value.describe_result(token)
        self.assertEqual(1, len(second['payload']['documents']))
        self.assertTrue(second['complete'])

        stream = value.execute(handle, {'source': json.dumps({
            'operation': 'watch', 'database': 'qualification',
            'collection': 'widgets', 'batch_size': 2,
        })})
        watched = value.describe_result(stream)
        self.assertTrue(watched['payload']['live'])
        self.assertIsNotNone(watched['payload']['resume_token'])
        self.assertTrue(value.cancel(stream))

    def test_document_pages_use_opaque_continuations_and_schema_sampling(self):
        def connector(**arguments):
            value = DriverClient(**arguments)
            value['qualification'].collections['widgets'] = Collection([
                {'_id': 1, 'optional': 'one'}, {'_id': 2}, {'_id': 3},
            ])
            return value

        value = client(connector)
        target = {
            'resource_kind': 'collection',
            'native': {
                'database': 'qualification', 'collection': 'widgets',
                'options': {},
            },
        }
        request = {
            'target_resource': target, 'limit': 2,
            '_provider_route': {
                'host': 'localhost', 'database': 'qualification',
            },
            'filter': {}, 'projection': {}, 'sort': [],
        }
        first = value.read_admin_rows(request)
        self.assertFalse(first['complete'])
        self.assertTrue(first['continuation'])
        optional = next(
            item for item in first['schema_sample']['fields']
            if item['path'] == 'optional'
        )
        self.assertEqual(1, optional['missing_count'])
        second = value.read_admin_rows({
            **request, 'continuation': first['continuation'],
        })
        self.assertTrue(second['complete'])


if __name__ == '__main__':
    unittest.main()
