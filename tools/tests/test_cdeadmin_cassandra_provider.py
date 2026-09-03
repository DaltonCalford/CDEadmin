##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Cassandra 5.0.8 provider, CQL boundary, and administration tests."""

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

from pgadmin.cdeadmin.providers.cassandra.client import (  # noqa: E402
    CassandraClient,
    CassandraClientError,
    CassandraDependencyError,
)
from pgadmin.cdeadmin.providers.cassandra.provider import (  # noqa: E402
    CassandraPilotProvider,
    PROFILE,
)
from pgadmin.cdeadmin.results.renderers import builtin_renderers  # noqa: E402


class SecretLease:
    def __init__(self, value=b'correct-horse'):
        self.value = bytearray(value)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        for index in range(len(self.value)):
            self.value[index] = 0
        self.closed = True

    def use(self, callback):
        return callback(memoryview(self.value))


class Statement:
    def __init__(self, query_string, **options):
        self.query_string = query_string
        self.options = options


class Result:
    def __init__(self, rows=(), names=None, types=None, more=False,
                 paging_state=None):
        self.current_rows = list(rows)
        self.column_names = names or (
            list(self.current_rows[0]) if self.current_rows else []
        )
        self.column_types = types or ['text'] * len(self.column_names)
        self.has_more_pages = more
        self.paging_state = paging_state
        self.was_applied = None
        self.warnings = []

    def __iter__(self):
        return iter(self.current_rows)

    def fetch_next_page(self):
        self.has_more_pages = False


class Future:
    warnings = ()
    trace_id = None

    def __init__(self, result):
        self.native_result = result
        self.cancelled = False

    def result(self, timeout=None):
        return self.native_result

    def cancel(self):
        self.cancelled = True
        return True


class Session:
    def __init__(self, cluster, keyspace):
        self.cluster = cluster
        self.keyspace = keyspace
        self.row_factory = None
        self.default_timeout = None
        self.closed = False

    def execute(self, statement, parameters=(), **options):
        source = getattr(statement, 'query_string', statement)
        self.cluster.factory.statements.append((source, parameters, options))
        if 'FROM system.local' in source:
            return Result([{
                'cluster_name': 'qualification',
                'release_version': '5.0.8', 'cql_version': '3.4.7',
                'native_protocol_version': '5', 'data_center': 'dc1',
                'rack': 'rack1', 'partitioner': 'Murmur3Partitioner',
                'host_id': 'host-one', 'broadcast_address': '127.0.0.1',
                'listen_address': '127.0.0.1', 'tokens': ['0'],
            }])
        if 'FROM system.peers_v2' in source:
            return Result([])
        if 'FROM system_schema.keyspaces' in source:
            return Result([{
                'keyspace_name': 'qualification',
                'durable_writes': True,
                'replication': {'class': 'SimpleStrategy'},
            }])
        if 'FROM system_schema.tables' in source:
            return Result([{
                'keyspace_name': 'qualification', 'table_name': 'widgets',
                'comment': 'test table',
            }])
        if 'FROM system_schema.columns' in source:
            return Result([
                {'keyspace_name': 'qualification', 'table_name': 'widgets',
                 'column_name': 'tenant', 'kind': 'partition_key',
                 'position': 0, 'type': 'text'},
                {'keyspace_name': 'qualification', 'table_name': 'widgets',
                 'column_name': 'id', 'kind': 'clustering',
                 'position': 0, 'type': 'int'},
                {'keyspace_name': 'qualification', 'table_name': 'widgets',
                 'column_name': 'value', 'kind': 'regular',
                 'position': -1, 'type': 'text'},
            ])
        if source.startswith('SELECT * FROM "qualification"."widgets"'):
            return Result([{'tenant': 'one', 'id': 1, 'value': 'before'}])
        if source.startswith('LIST ROLES'):
            return Result([{'role': 'operator', 'can_login': True}])
        if source.startswith('LIST ALL PERMISSIONS'):
            return Result([{'role': 'operator', 'permission': 'SELECT'}])
        if ('system_schema' in source or 'system_traces' in source or
                'system_views' in source):
            return Result([])
        return Result([{'ok': True}])

    def execute_async(self, statement, parameters=()):
        source = statement.query_string
        self.cluster.factory.statements.append((source, parameters, {}))
        return Future(Result(
            [{'tenant': 'one', 'id': 1, 'payload': b'bytes'}],
            ['tenant', 'id', 'payload'], ['text', 'int', 'blob'],
        ))

    def shutdown(self):
        self.closed = True


class DriverCluster:
    def __init__(self, factory, options):
        self.factory = factory
        self.options = options
        self.sessions = []
        self.closed = False
        self.metadata = SimpleNamespace(check_schema_agreement=lambda: True)

    def connect(self, keyspace=None):
        value = Session(self, keyspace)
        self.sessions.append(value)
        return value

    def shutdown(self):
        self.closed = True


class ClusterFactory:
    def __init__(self):
        self.clusters = []
        self.statements = []

    def __call__(self, **options):
        value = DriverCluster(self, options)
        self.clusters.append(value)
        return value


class Consistency:
    ANY = 0
    ONE = 1
    TWO = 2
    THREE = 3
    QUORUM = 4
    ALL = 5
    LOCAL_QUORUM = 6
    EACH_QUORUM = 7
    SERIAL = 8
    LOCAL_SERIAL = 9
    LOCAL_ONE = 10


class AuthProvider:
    def __init__(self, username, password):
        self.username = username
        self.password = password


def fake_module(version='3.30.1'):
    factory = ClusterFactory()
    return SimpleNamespace(
        __version__=version, Cluster=factory,
        PlainTextAuthProvider=AuthProvider, ConsistencyLevel=Consistency,
        SimpleStatement=Statement, dict_factory=lambda *_args: None,
        DCAwareRoundRobinPolicy=lambda **values: values,
        RoundRobinPolicy=lambda: {'kind': 'round-robin'},
        TokenAwarePolicy=lambda child: {'kind': 'token-aware',
                                        'child': child},
        ExponentialReconnectionPolicy=lambda *values: values,
    ), factory


def route(**changes):
    value = {
        'host': '127.0.0.1', 'port': 9042, 'local_dc': 'dc1',
        'keyspace': 'qualification', 'tls_mode': 'disabled',
        'consistency': 'LOCAL_ONE',
        'serial_consistency': 'LOCAL_SERIAL',
    }
    value.update(changes)
    return value


def client(secret_acquirer=None):
    module, factory = fake_module()
    acquire = secret_acquirer or (lambda *_args: SecretLease())
    return CassandraClient(acquire, module), factory


def table_target():
    return {
        'resource_kind': 'table',
        'extensions': {'cassandra': {'native': {
            'keyspace_name': 'qualification', 'table_name': 'widgets',
            'columns': [
                {'column_name': 'tenant', 'kind': 'partition_key',
                 'position': 0, 'type': 'text'},
                {'column_name': 'id', 'kind': 'clustering',
                 'position': 0, 'type': 'int'},
                {'column_name': 'value', 'kind': 'regular',
                 'position': -1, 'type': 'text'},
            ],
        }}},
    }


def context():
    return SimpleNamespace(
        endpoint_id='endpoint', mode='legacy_native',
        runtime_verification_state='verified',
        verified_runtime_family='cassandra',
        declared_runtime_family='cassandra',
        effective_permissions=frozenset({
            'network', 'secret_read', 'data_read', 'data_write',
            'administer', 'execute', 'filesystem',
        }), session_namespace='session', cache_namespace='cache',
    )


class Permissions:
    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True


class CassandraProviderTests(unittest.TestCase):

    def test_non_lwt_result_does_not_probe_was_applied_as_failure(self):
        class NonLwtResult:
            @property
            def was_applied(self):
                raise RuntimeError(
                    'LWT result should have exactly one row. This has 0.'
                )

        self.assertIsNone(CassandraClient._was_applied(NonLwtResult()))

    def test_profile_and_production_wide_column_renderer(self):
        self.assertEqual('wide-column', PROFILE.model_family)
        self.assertEqual('wide_column', PROFILE.result_kind)
        self.assertEqual('5.0.8', PROFILE.exact_version)
        renderer = next(
            item for item in builtin_renderers()
            if item.renderer_id == 'cdeadmin.result.wide-column.grid'
        )
        self.assertFalse(renderer.fixture_safe)
        self.assertTrue(renderer.worker_required)
        self.assertEqual(
            frozenset({'csv', 'json', 'jsonl'}), renderer.export_formats
        )

    def test_exact_driver_version_fails_closed(self):
        module, _factory = fake_module('3.29.2')
        with self.assertRaisesRegex(
            CassandraDependencyError, 'qualified 3.30.1'
        ):
            CassandraClient(module=module)

    def test_route_secret_and_runtime_identity_are_native(self):
        leases = []

        def acquire(*_args):
            value = SecretLease()
            leases.append(value)
            return value

        adapter, factory = client(acquire)
        identity = adapter.runtime_identity({'route': route(
            username='operator', credential_reference_id='credential-one',
            principal_reference='principal-one',
        )})
        self.assertEqual('5.0.8', identity['version'])
        self.assertEqual('cql', identity['protocol_id'])
        auth = factory.clusters[0].options['auth_provider']
        self.assertEqual('operator', auth.username)
        self.assertEqual('correct-horse', auth.password)
        self.assertTrue(leases[0].closed)
        self.assertEqual({0}, set(leases[0].value))

    def test_connection_policy_controls_are_forwarded_to_driver(self):
        adapter, factory = client()
        handle = adapter.open_session({'route': route(
            load_balancing_policy='token-aware-dc',
            used_hosts_per_remote_dc=2,
            allow_remote_dcs_for_local_cl=True,
            control_connection_timeout=12,
            heartbeat_interval=15,
            heartbeat_timeout=20,
            schema_agreement_timeout=25,
            reconnect_base_delay=2,
            reconnect_max_delay=40,
            reconnect_max_attempts=8,
            executor_threads=4,
            application_name='CDEadmin-test',
        )})
        options = factory.clusters[0].options
        self.assertEqual(12.0, options['control_connection_timeout'])
        self.assertEqual(15.0, options['idle_heartbeat_interval'])
        self.assertEqual(20.0, options['idle_heartbeat_timeout'])
        self.assertEqual(25.0, options['max_schema_agreement_wait'])
        self.assertEqual((2, 40, 8), options['reconnection_policy'])
        self.assertEqual('token-aware', options['load_balancing_policy'][
            'kind'])
        self.assertEqual(2, options['load_balancing_policy']['child'][
            'used_hosts_per_remote_dc'])
        self.assertEqual('CDEadmin-test', options['application_name'])
        handle.close()

    def test_password_auth_requires_both_user_and_typed_reference(self):
        adapter, _factory = client()
        for value in (
            route(auth_mode='password'),
            route(auth_mode='password', username='operator'),
        ):
            with self.assertRaisesRegex(
                CassandraClientError, 'requires username and credential'
            ):
                adapter.open_session({'route': value})

    def test_route_rejects_uri_unknown_fields_and_old_protocol(self):
        adapter, _factory = client()
        for value in (
            {**route(), 'uri': 'cassandra://hidden'},
            {**route(), 'password': 'inline'},
            {**route(), 'protocol_version': 4},
        ):
            with self.assertRaises(CassandraClientError):
                adapter.runtime_identity({'route': value})

    def test_cql_result_and_transaction_keep_native_outcomes_opaque(self):
        adapter, _factory = client()
        handle = adapter.open_session({'route': route()})
        token = adapter.execute(handle, {
            'source': 'SELECT * FROM qualification.widgets',
            'parameters': (),
        })
        result = adapter.describe_result(token)
        self.assertEqual('wide_column', result['result_kind'])
        self.assertEqual('Ynl0ZXM=', result['payload']['rows'][0][
            'payload'
        ]['$binary'])
        transaction = adapter.describe_transaction(handle)
        self.assertFalse(transaction['common_finality_inference'])
        self.assertFalse(transaction['retry_decision_owned_by_common_code'])

    def test_discovery_augments_tables_and_uses_unique_resource_ids(self):
        adapter, factory = client()
        resources = adapter.list_resources({'route': route()})
        self.assertEqual(1, len(factory.clusters))
        kinds = {item['resource_kind'] for item in resources}
        self.assertTrue({
            'cluster', 'datacenter', 'node', 'keyspace', 'table', 'column',
            'role', 'permission', 'repair', 'snapshot', 'shell',
        }.issubset(kinds))
        columns = [item for item in resources
                   if item['resource_kind'] == 'column']
        self.assertEqual(len(columns), len({item['resource_id']
                                           for item in columns}))
        table = next(item for item in resources
                     if item['resource_kind'] == 'table')
        self.assertEqual(['tenant', 'id'], table['native']['primary_key'])

    def test_structured_schema_and_security_plans_never_accept_raw_cql(self):
        adapter, _factory = client()
        plan = adapter.plan_admin_operation({
            'resource_kind': 'table', 'operation_id': 'create',
            'draft': {
                'keyspace': 'qualification', 'name': 'events',
                'columns': [
                    {'name': 'tenant', 'type': 'text'},
                    {'name': 'event_id', 'type': 'timeuuid'},
                ],
                'partition_keys': ['tenant'],
                'clustering_keys': ['event_id'], 'options': {},
            }, '_provider_route': route(),
        })
        statement = plan['command_preview']['statements'][0]
        self.assertIn('PRIMARY KEY ("tenant", "event_id")', statement)
        self.assertNotIn('raw_cql', str(plan))
        with self.assertRaises(CassandraClientError):
            adapter._type_statement('create', {
                'keyspace': 'qualification', 'name': 'bad',
                'fields': [{'name': 'field', 'type': 'text); DROP KEYSPACE'}],
            }, {})

    def test_column_rename_is_limited_to_primary_key_columns(self):
        adapter, _factory = client()
        native = {
            'keyspace_name': 'qualification', 'table_name': 'widgets',
            'column_name': 'value', 'kind': 'regular',
        }
        with self.assertRaisesRegex(
            CassandraClientError, 'only primary-key columns'
        ):
            adapter._column_statement(
                'rename', {'new_name': 'renamed_value'}, native
            )
        native['column_name'] = 'id'
        native['kind'] = 'clustering'
        statement = adapter._column_statement(
            'rename', {'new_name': 'item_id'}, native
        )
        self.assertIn('RENAME "id" TO "item_id"', statement['source'])

    def test_row_identity_survives_preview_then_is_single_use_on_apply(self):
        adapter, factory = client()
        target = table_target()
        page = adapter.read_admin_rows({
            '_provider_route': route(), 'target_resource': target,
        })
        token = page['rows'][0]['identity_token']
        request = {
            'resource_kind': 'table', 'operation_id': 'update',
            'target_resource': target,
            'draft': {'selector': {'identity_token': token},
                      'changes': {'value': 'after'}},
            '_provider_route': route(),
        }
        plan = adapter.plan_admin_operation(request)
        adapter.apply_admin_operation({
            'provider_payload': plan['provider_payload']
        })
        source, parameters, _options = factory.statements[-1]
        self.assertEqual(
            ('after', 'one', 1), parameters
        )
        self.assertIn('WHERE "tenant" = %s AND "id" = %s', source)
        with self.assertRaisesRegex(CassandraClientError, 'stale'):
            adapter.apply_admin_operation({
                'provider_payload': plan['provider_payload']
            })

    def test_row_identity_is_bound_to_endpoint_route(self):
        adapter, _factory = client()
        target = table_target()
        page = adapter.read_admin_rows({
            '_provider_route': route(), 'target_resource': target,
        })
        with self.assertRaisesRegex(CassandraClientError, 'another endpoint'):
            adapter.plan_admin_operation({
                'resource_kind': 'table', 'operation_id': 'delete',
                'target_resource': target,
                'draft': {'selector': {
                    'identity_token': page['rows'][0]['identity_token'],
                }}, '_provider_route': route(host='127.0.0.2'),
            })

    def test_tool_plan_does_not_compile_as_cql(self):
        adapter, _factory = client()
        plan = adapter.plan_admin_operation({
            'resource_kind': 'repair', 'operation_id': 'execute',
            'draft': {'action': 'repair',
                      'arguments': {'keyspace': 'qualification'}},
            '_provider_route': route(tool_workspace='/tmp/cdeadmin-tools'),
        })
        self.assertEqual('repair', plan['command_preview']['tool'][
            'provider_tool'
        ])
        self.assertEqual([], plan['command_preview']['statements'])

    def test_cqlsh_tls_command_cannot_downgrade_to_plaintext(self):
        adapter, _factory = client()
        executable, command = adapter._tool_command(
            'shell', 'file', {'path': 'query.cql'},
            adapter._route({'route': route(
                tls_mode='self-signed',
                tool_workspace='/tmp/cdeadmin-tools',
            )}),
        )
        self.assertEqual('cqlsh', executable)
        self.assertIn('--ssl', command)
        with self.assertRaisesRegex(
            CassandraClientError, 'plaintext downgrade is refused'
        ):
            adapter.apply_admin_operation({'provider_payload': {
                'resource_kind': 'shell', 'operation_id': 'execute',
                'draft': {
                    'action': 'file',
                    'arguments': {'path': 'query.cql'},
                },
                'native': {},
                '_provider_route': route(
                    tls_mode='system-ca',
                    tool_workspace='/tmp/cdeadmin-tools',
                ),
            }})

    def test_descriptor_and_manifest_are_live_qualified(self):
        adapter, _factory = client()
        descriptor = CassandraPilotProvider(
            context(), Permissions(), adapter
        ).visual_admin_descriptor()
        self.assertEqual('cassandra-cql-structured-planner', descriptor[
            'native_planner'
        ])
        self.assertTrue(descriptor['native_outcomes_are_opaque'])
        table = next(item for item in descriptor['objects']
                     if item['resource_kind'] == 'table')
        self.assertTrue(next(
            item for item in table['operations']
            if item['operation_id'] == 'create'
        )['execution_available'])
        manifest = json.loads((
            WEB / 'pgadmin/cdeadmin/providers/cassandra/'
            'provider_manifest.json'
        ).read_text(encoding='utf-8'))
        self.assertTrue(manifest['enabled'])
        self.assertTrue(manifest['production_registration'])
        self.assertEqual('experimental', manifest['support_state'])
        matrix = json.loads((
            WEB / 'pgadmin/cdeadmin/providers/cassandra/'
            'compatibility_matrix.json'
        ).read_text(encoding='utf-8'))
        self.assertEqual(
            ['5.0.8'], matrix['patch_policy']['qualified_versions']
        )
        linux = next(
            item for item in matrix['client_platforms']
            if item['platform'] == 'linux'
        )
        self.assertEqual('passed', linux['live_suite'])


if __name__ == '__main__':
    unittest.main()
