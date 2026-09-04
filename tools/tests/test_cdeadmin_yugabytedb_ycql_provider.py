##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""YugabyteDB 2025.2.2.2 YCQL provider and visual-admin tests."""

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

from pgadmin.cdeadmin.providers import (  # noqa: E402
    register_builtin_providers,
)
from pgadmin.cdeadmin.providers.yugabytedb_ycql.client import (  # noqa: E402
    YugabyteDBYCQLClient,
    YugabyteDBYCQLClientError,
)
from pgadmin.cdeadmin.providers.yugabytedb_ycql.provider import (  # noqa: E402
    PROFILE,
    YugabyteDBYCQLProvider,
)


class Statement:
    def __init__(self, query_string, **options):
        self.query_string = query_string
        self.options = options


class Result:
    def __init__(self, rows=(), names=None):
        self.current_rows = list(rows)
        self.column_names = names or (
            list(self.current_rows[0]) if self.current_rows else []
        )
        self.column_types = ['text'] * len(self.column_names)
        self.has_more_pages = False
        self.paging_state = None
        self.was_applied = None
        self.warnings = []

    def __iter__(self):
        return iter(self.current_rows)


class Future:
    warnings = ()
    trace_id = None

    def __init__(self, result):
        self.native_result = result

    def result(self):
        return self.native_result

    @staticmethod
    def cancel():
        return True


class VersionResponse:
    def __init__(self, version):
        self.payload = json.dumps({
            'version_number': version,
            'build_number': '11',
            'git_hash': '1db6d154f7588f19bc2ae02d0c9b7e5ef211ba00',
            'build_type': 'RELEASE',
        }).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return self.payload


class Session:
    def __init__(self, cluster, keyspace):
        self.cluster = cluster
        self.keyspace = keyspace
        self.row_factory = None
        self.default_timeout = None

    def execute(self, statement, parameters=(), **options):
        source = getattr(statement, 'query_string', statement)
        self.cluster.factory.statements.append((source, parameters, options))
        if 'FROM system.local' in source:
            return Result([{
                'cluster_name': 'ycql-qualification',
                'release_version': '3.9-SNAPSHOT',
                'cql_version': '3.4.2',
                'native_protocol_version': self.cluster.factory.protocol,
                'data_center': 'datacenter1', 'rack': 'rack1',
                'partitioner': 'org.apache.cassandra.dht.Murmur3Partitioner',
                'host_id': 'host-one', 'broadcast_address': '127.0.0.1',
                'listen_address': '127.0.0.1', 'tokens': ['0'],
            }])
        if 'FROM system.peers_v2' in source:
            return Result([])
        if 'FROM system_schema.keyspaces' in source:
            return Result([{
                'keyspace_name': 'qualification',
                'durable_writes': True,
                'replication': {'class': 'NetworkTopologyStrategy'},
            }])
        if 'FROM system_schema.tables' in source:
            return Result([{
                'keyspace_name': 'qualification',
                'table_name': 'events',
            }])
        if 'FROM system_schema.columns' in source:
            return Result([
                {'keyspace_name': 'qualification', 'table_name': 'events',
                 'column_name': 'tenant', 'kind': 'partition_key',
                 'position': 0, 'type': 'text'},
                {'keyspace_name': 'qualification', 'table_name': 'events',
                 'column_name': 'id', 'kind': 'clustering',
                 'position': 0, 'type': 'int'},
                {'keyspace_name': 'qualification', 'table_name': 'events',
                 'column_name': 'value', 'kind': 'regular',
                 'position': -1, 'type': 'text'},
            ])
        if 'FROM system_schema.types' in source:
            return Result([{
                'keyspace_name': 'qualification', 'type_name': 'address',
                'field_names': ['city'], 'field_types': ['text'],
            }])
        if source.startswith('SELECT * FROM "qualification"."events"'):
            return Result([{'tenant': 'one', 'id': 1, 'value': 'before'}])
        if 'FROM system_auth.roles' in source:
            return Result([{'role': 'operator', 'can_login': True}])
        if 'FROM system_auth.role_permissions' in source:
            return Result([
                {
                    'role': 'operator',
                    'resource': 'data/qualification/events',
                    'permissions': ['SELECT'],
                },
                {
                    'role': 'operator',
                    'resource': 'data/qualification/archive',
                    'permissions': ['MODIFY'],
                },
            ])
        if 'system_schema' in source:
            return Result([])
        return Result([{'ok': True}])

    def execute_async(self, statement, parameters=()):
        source = statement.query_string
        self.cluster.factory.statements.append((source, parameters, {}))
        return Future(Result([{'tenant': 'one', 'id': 1}]))

    @staticmethod
    def shutdown():
        return None


class DriverCluster:
    def __init__(self, factory, options):
        self.factory = factory
        self.options = options
        self.metadata = SimpleNamespace(check_schema_agreement=lambda: True)

    def connect(self, keyspace=None):
        return Session(self, keyspace)

    @staticmethod
    def shutdown():
        return None


class ClusterFactory:
    def __init__(self, version='2025.2.2.2', protocol='4'):
        self.version = version
        self.protocol = protocol
        self.clusters = []
        self.statements = []

    def __call__(self, **options):
        cluster = DriverCluster(self, options)
        self.clusters.append(cluster)
        return cluster


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


def fake_module(version='2025.2.2.2', protocol='4'):
    factory = ClusterFactory(version, protocol)
    module = SimpleNamespace(
        __version__='3.30.1', Cluster=factory,
        PlainTextAuthProvider=lambda **values: values,
        ConsistencyLevel=Consistency, SimpleStatement=Statement,
        dict_factory=lambda *_args: None,
        DCAwareRoundRobinPolicy=lambda **values: values,
    )
    return module, factory


def route(**changes):
    value = {
        'host': '127.0.0.1', 'port': 9042,
        'local_dc': 'datacenter1', 'keyspace': 'qualification',
        'tls_mode': 'disabled', 'consistency': 'LOCAL_ONE',
        'serial_consistency': 'LOCAL_SERIAL',
    }
    value.update(changes)
    return value


def adapter(version='2025.2.2.2', protocol='4'):
    module, factory = fake_module(version, protocol)

    def version_urlopen(*_args, **_values):
        return VersionResponse(version)

    return YugabyteDBYCQLClient(
        module=module, version_urlopen=version_urlopen
    ), factory


def context():
    return SimpleNamespace(
        endpoint_id='endpoint', mode='legacy_native',
        runtime_verification_state='verified',
        verified_runtime_family='yugabytedb',
        declared_runtime_family='yugabytedb',
        effective_permissions=frozenset({
            'network', 'secret_read', 'data_read', 'data_write',
            'administer', 'execute',
        }),
        session_namespace='session', cache_namespace='cache',
    )


class Permissions:
    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True


class YugabyteDBYCQLProviderTests(unittest.TestCase):

    def test_profile_and_builtin_registration_are_distinct_from_ysql(self):
        self.assertEqual('yugabytedb', PROFILE.engine_id)
        self.assertEqual('yugabytedb-ycql', PROFILE.profile_id)
        self.assertEqual('cql', PROFILE.protocol_id)

        class Registry:
            packages = []

            def register_package(self, manifest, module_name):
                value = (manifest, module_name)
                self.packages.append(value)
                return value

        registry = Registry()
        register_builtin_providers(registry)
        profiles = {
            item[0]['identity']['profile_id'] for item in registry.packages
        }
        self.assertIn('yugabytedb-native', profiles)
        self.assertIn('yugabytedb-ycql', profiles)

    def test_route_pins_protocol_v4_and_identity_normalizes_build_suffix(self):
        client, factory = adapter()
        identity = client.runtime_identity({'route': route()})
        self.assertEqual('yugabytedb', identity['engine_id'])
        self.assertEqual('2025.2.2.2', identity['version'])
        self.assertEqual('3.9-SNAPSHOT', identity['native'][
            'ycql_compatibility_release'
        ])
        self.assertEqual(4, factory.clusters[0].options['protocol_version'])
        with self.assertRaisesRegex(
            YugabyteDBYCQLClientError, 'protocol version 4'
        ):
            client.runtime_identity({'route': route(protocol_version=5)})
        with self.assertRaisesRegex(
            YugabyteDBYCQLClientError, 'version API host is invalid'
        ):
            client.runtime_identity({'route': route(
                version_api_host='trusted.example/redirect'
            )})

    def test_identity_rejects_wrong_release_or_negotiated_protocol(self):
        for version, protocol in (
            ('2025.2.2.1', '4'), ('2025.2.2.2', '5'),
        ):
            with self.subTest(version=version, protocol=protocol):
                client, _factory = adapter(version, protocol)
                with self.assertRaises(YugabyteDBYCQLClientError):
                    client.runtime_identity({'route': route()})

    def test_discovery_only_queries_and_returns_documented_ycql_surface(self):
        client, factory = adapter()
        resources = client.list_resources({'route': route()})
        kinds = {item['resource_kind'] for item in resources}
        self.assertTrue({
            'cluster', 'datacenter', 'node', 'keyspace', 'table', 'column',
            'user-defined-type', 'role', 'permission', 'query',
        }.issubset(kinds))
        self.assertFalse({
            'materialized-view', 'function', 'aggregate',
            'tracing-session', 'repair', 'snapshot', 'shell',
        }.intersection(kinds))
        queried = '\n'.join(item[0] for item in factory.statements)
        self.assertNotIn('system_schema.views', queried)
        self.assertNotIn('system_schema.functions', queried)
        self.assertNotIn('system_traces', queried)
        self.assertTrue(all(
            item['resource_id'].startswith('yugabytedb:ycql:')
            for item in resources
        ))
        permissions = [
            item for item in resources
            if item['resource_kind'] == 'permission'
        ]
        self.assertEqual(
            len(permissions),
            len({item['resource_id'] for item in permissions}),
        )

    def test_table_and_index_plans_are_structured_ycql(self):
        client, _factory = adapter()
        plan = client.plan_admin_operation({
            'resource_kind': 'table', 'operation_id': 'create',
            'draft': {
                'keyspace': 'qualification', 'name': 'events',
                'columns': [
                    {'name': 'tenant', 'type': 'text'},
                    {'name': 'event_id', 'type': 'timeuuid'},
                ],
                'partition_keys': ['tenant'],
                'clustering_keys': ['event_id'],
                'tablets': 8, 'transactions_enabled': True,
                'transaction_consistency': 'strong',
            },
            '_provider_route': route(),
        })
        statement = plan['command_preview']['statements'][0]
        self.assertIn('tablets = 8', statement)
        self.assertIn("transactions = {'enabled': true", statement)
        self.assertEqual('YCQL', plan['command_preview']['language'])
        self.assertNotIn('raw_cql', json.dumps(plan))

        index = client.plan_admin_operation({
            'resource_kind': 'index', 'operation_id': 'create',
            'draft': {
                'keyspace': 'qualification', 'table': 'events',
                'name': 'events_value', 'target': 'value',
            },
            '_provider_route': route(),
        })
        index_source = index['command_preview']['statements'][0]
        self.assertTrue(index_source.startswith('CREATE INDEX'))
        self.assertNotIn('CUSTOM', index_source)

    def test_ycql_specific_validation_and_permissions_fail_closed(self):
        client, _factory = adapter()
        request = {
            'resource_kind': 'table', 'operation_id': 'create',
            'draft': {
                'keyspace': 'qualification', 'name': 'events',
                'columns': [{'name': 'tenant', 'type': 'text'}],
                'partition_keys': ['tenant'], 'tablets': 'many',
            },
            '_provider_route': route(),
        }
        with self.assertRaisesRegex(
            YugabyteDBYCQLClientError, 'tablet count'
        ):
            client.plan_admin_operation(request)
        with self.assertRaisesRegex(
            YugabyteDBYCQLClientError, 'permission is invalid'
        ):
            client._security_statements(
                'permission', 'grant', {
                    'principal': 'operator', 'privileges': ['MASK'],
                }, {}, True,
            )

    def test_visual_catalog_has_ycql_forms_and_no_cassandra_only_actions(self):
        client, _factory = adapter()
        descriptor = YugabyteDBYCQLProvider(
            context(), Permissions(), client
        ).visual_admin_descriptor()
        self.assertEqual(
            'yugabytedb-ycql-structured-planner',
            descriptor['native_planner'],
        )
        self.assertEqual(['wide_column'], descriptor['experience_families'])
        self.assertEqual(
            {'wide_column'}, set(descriptor['concept_declarations'])
        )
        self.assertEqual(
            'not_applicable',
            descriptor['concept_declarations']['wide_column'][
                'materialized_views'
            ]['status'],
        )
        table = next(
            item for item in descriptor['objects']
            if item['resource_kind'] == 'table'
        )
        create = next(
            item for item in table['operations']
            if item['operation_id'] == 'create'
        )
        fields = {item['field_id'] for item in create['form']['fields']}
        self.assertIn('tablets', fields)
        self.assertIn('transactions_enabled', fields)
        udt = next(
            item for item in descriptor['objects']
            if item['resource_kind'] == 'user-defined-type'
        )
        self.assertTrue(any(
            item['execution_available'] for item in udt['operations']
        ))
        self.assertNotIn(
            'alter',
            {item['operation_id'] for item in udt['operations']},
        )
        column = next(
            item for item in descriptor['objects']
            if item['resource_kind'] == 'column'
        )
        self.assertNotIn(
            'alter',
            {item['operation_id'] for item in column['operations']},
        )
        advertised = {
            item['resource_kind'] for item in descriptor['objects']
        }
        self.assertNotIn('materialized-view', advertised)
        self.assertNotIn('function', advertised)

    def test_manifest_records_ycql_endpoint_contract(self):
        manifest = json.loads((
            WEB / 'pgadmin/cdeadmin/providers/yugabytedb_ycql/'
            'provider_manifest.json'
        ).read_text(encoding='utf-8'))
        self.assertEqual('yugabytedb-ycql', manifest['identity'][
            'profile_id'
        ])
        self.assertEqual(9042, manifest['registration']['default_port'])
        self.assertEqual(
            'cassandra-driver-3.30.1-ycql-v4',
            manifest['registration']['target_adapter_version'],
        )
        self.assertEqual({
            'engine_id': 'yugabytedb',
            'engine_display_name': 'YugabyteDB 2025.2.2.2',
            'interface_id': 'ycql',
            'interface_display_name': 'YCQL',
            'protocol_id': 'cql',
        }, manifest['registration']['interface'])
        self.assertEqual(
            'passed', manifest['provenance']['activation_gate']
        )
        self.assertEqual(
            'passed', manifest['provenance']['dual_interface_gate']
        )
        matrix = json.loads((
            WEB / 'pgadmin/cdeadmin/providers/yugabytedb_ycql/'
            'compatibility_matrix.json'
        ).read_text(encoding='utf-8'))
        linux = next(
            item for item in matrix['client_platforms']
            if item['platform'] == 'linux'
        )
        self.assertEqual('passed', linux['live_suite'])


if __name__ == '__main__':
    unittest.main()
