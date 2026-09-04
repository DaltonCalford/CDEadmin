##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Actual-engine provider pilot tests for CDE-PREP-150."""

from __future__ import annotations

import json
import sys
import unittest
import uuid
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.contracts.v1.runtime import (  # noqa: E402
    load_contract_schema,
    validate_contract,
)
from pgadmin.cdeadmin.core import EndpointContext  # noqa: E402
from pgadmin.cdeadmin.sdk import (  # noqa: E402
    PilotProviderError,
    RuntimeIdentityError,
)
from pgadmin.cdeadmin.semantic_models import (  # noqa: E402
    SemanticCompilationUnavailable,
)
from pgadmin.cdeadmin.providers.clickhouse.provider import (  # noqa: E402
    ClickHousePilotProvider,
    PROFILE as CLICKHOUSE,
)
from pgadmin.cdeadmin.providers.cassandra.provider import (  # noqa: E402
    CassandraPilotProvider,
    PROFILE as CASSANDRA,
)
from pgadmin.cdeadmin.providers.duckdb.provider import (  # noqa: E402
    DuckDBPilotProvider,
    PROFILE as DUCKDB,
)
from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    FirebirdProvider,
    PROFILE as FIREBIRD,
)
from pgadmin.cdeadmin.providers.influxdb.provider import (  # noqa: E402
    InfluxDBPilotProvider,
    PROFILE as INFLUXDB,
)
from pgadmin.cdeadmin.providers.milvus.provider import (  # noqa: E402
    MilvusPilotProvider,
    PROFILE as MILVUS,
)
from pgadmin.cdeadmin.providers.mongodb.provider import (  # noqa: E402
    MongoDBPilotProvider,
    PROFILE as MONGODB,
)
from pgadmin.cdeadmin.providers.mysql_family.provider import (  # noqa: E402
    MARIADB_PROFILE,
    MYSQL_PROFILE,
    MariaDBPilotProvider,
    MySQLPilotProvider,
)
from pgadmin.cdeadmin.providers.neo4j.provider import (  # noqa: E402
    Neo4jPilotProvider,
    PROFILE as NEO4J,
)
from pgadmin.cdeadmin.providers.opensearch.provider import (  # noqa: E402
    OpenSearchPilotProvider,
    PROFILE as OPENSEARCH,
)
from pgadmin.cdeadmin.providers.opensearch_sql_ppl.provider import (  # noqa: E402
    OpenSearchSQLPPLPilotProvider,
    PROFILE as OPENSEARCH_SQL_PPL,
)
from pgadmin.cdeadmin.providers.redis.provider import (  # noqa: E402
    RedisPilotProvider,
    PROFILE as REDIS,
)
from pgadmin.cdeadmin.providers import (  # noqa: E402
    register_builtin_providers,
)
from pgadmin.cdeadmin.providers.sqlite.provider import (  # noqa: E402
    SQLiteProvider,
    PROFILE as SQLITE,
)
from pgadmin.cdeadmin.providers.xtdb.provider import (  # noqa: E402
    XTDBPilotProvider,
    PROFILE as XTDB,
)
from pgadmin.cdeadmin.providers.apache_ignite.provider import (  # noqa: E402
    ApacheIgniteProvider, PROFILE as APACHE_IGNITE,
)
from pgadmin.cdeadmin.providers.cockroachdb.provider import (  # noqa: E402
    CockroachDBProvider, PROFILE as COCKROACHDB,
)
from pgadmin.cdeadmin.providers.dolt.provider import (  # noqa: E402
    DoltProvider, PROFILE as DOLT,
)
from pgadmin.cdeadmin.providers.foundationdb.provider import (  # noqa: E402
    FoundationDBProvider, PROFILE as FOUNDATIONDB,
)
from pgadmin.cdeadmin.providers.tidb.provider import (  # noqa: E402
    TiDBProvider, PROFILE as TIDB,
)
from pgadmin.cdeadmin.providers.tikv.provider import (  # noqa: E402
    TiKVProvider, PROFILE as TIKV,
)
from pgadmin.cdeadmin.providers.vitess.provider import (  # noqa: E402
    VitessProvider, PROFILE as VITESS,
)
from pgadmin.cdeadmin.providers.yugabytedb.provider import (  # noqa: E402
    YugabyteDBProvider, PROFILE as YUGABYTEDB,
)
from tools.cdeadmin_actual_engine_gate import evaluate  # noqa: E402


PILOTS = (
    (MySQLPilotProvider, MYSQL_PROFILE),
    (MariaDBPilotProvider, MARIADB_PROFILE),
    (MongoDBPilotProvider, MONGODB),
    (Neo4jPilotProvider, NEO4J),
    (CassandraPilotProvider, CASSANDRA),
    (RedisPilotProvider, REDIS),
    (XTDBPilotProvider, XTDB),
    (ClickHousePilotProvider, CLICKHOUSE),
    (InfluxDBPilotProvider, INFLUXDB),
    (MilvusPilotProvider, MILVUS),
    (OpenSearchPilotProvider, OPENSEARCH),
    (OpenSearchSQLPPLPilotProvider, OPENSEARCH_SQL_PPL),
    (DuckDBPilotProvider, DUCKDB),
    (FirebirdProvider, FIREBIRD),
    (SQLiteProvider, SQLITE),
    (ApacheIgniteProvider, APACHE_IGNITE),
    (CockroachDBProvider, COCKROACHDB),
    (DoltProvider, DOLT),
    (FoundationDBProvider, FOUNDATIONDB),
    (TiDBProvider, TIDB),
    (TiKVProvider, TIKV),
    (VitessProvider, VITESS),
    (YugabyteDBProvider, YUGABYTEDB),
)
PROVIDER_ROOT = WEB / 'pgadmin/cdeadmin/providers'
RECORD_PATH = PROVIDER_ROOT / 'actual_engine_pilots.json'


def semantic_model(engine_id):
    return {
        'contract_version': '1.0.0',
        'name': 'Provider matrix model',
        'description': 'Semantic compiler admission test',
        'sources': [{
            'id': 'facts',
            'resource_id': f'{engine_id}:facts',
            'relation': ['facts'],
            'alias': 'facts',
        }],
        'joins': [],
        'dimensions': [],
        'measures': [{
            'id': 'row_count',
            'name': 'Row count',
            'aggregation': 'count',
            'field': None,
            'format': '0',
        }],
        'default_filters': [],
        'materializations': [],
        'security': {},
        'annotations': {'qualification': True},
    }


def semantic_query():
    return {
        'axes': {'rows': [], 'columns': [], 'pages': []},
        'measures': ['row_count'],
        'filters': [],
        'totals': False,
        'limit': 10,
    }


def context(profile, label='one'):
    endpoint = uuid.uuid5(uuid.NAMESPACE_URL, f'{profile.engine_id}:{label}')

    def child(purpose):
        return str(uuid.uuid5(endpoint, purpose))

    return EndpointContext(
        endpoint_id=str(endpoint), mode='legacy_native',
        experience_family=profile.engine_id,
        provider_id=profile.provider_id, provider_version='0.1.0',
        profile_id=profile.profile_id, profile_version=profile.exact_version,
        target_adapter_id=f'{profile.protocol_id}-client',
        target_adapter_version='unselected', pool_namespace=child('pool'),
        session_namespace=child('session'), cache_namespace=child('cache'),
        diagnostic_namespace=child('diagnostic'),
        effective_permissions=frozenset({
            'network', 'secret_read', 'data_read', 'data_write',
            'administer', 'execute', 'embedded_runtime', 'filesystem',
        }),
    )


class Permissions:
    def __init__(self):
        self.calls = []

    def require(self, permission, scope='endpoint'):
        self.calls.append((permission, scope))


class Client:
    def __init__(self, profile):
        self.profile = profile
        self.closed = False
        self.cancelled = []

    def runtime_identity(self):
        return {
            'engine_id': self.profile.engine_id,
            'version': self.profile.exact_version,
            'build_id': f'{self.profile.engine_id}-exact-build',
            'protocol_id': self.profile.protocol_id,
        }

    def list_resources(self, _request):
        kind = self.profile.resource_kinds[0]
        return [{
            'resource_id': f'{self.profile.engine_id}:{kind}:one',
            'resource_kind': kind, 'display_name': 'one',
            'authority_path': [self.profile.engine_id, kind, 'one'],
            'generation': 'generation-one',
        }]

    def inspect_resource(self, request):
        kind = self.profile.resource_kinds[0]
        return {
            'resource_id': request.get(
                'resource_id', f'{self.profile.engine_id}:{kind}:one'
            ),
            'resource_kind': kind, 'display_name': 'one',
            'authority_path': [self.profile.engine_id, kind, 'one'],
            'generation': 'generation-one',
        }

    def open_session(self, _request):
        return object()

    def describe_transaction(self, _handle):
        return {
            'native_state': f'{self.profile.engine_id}-opaque',
            'finality_interpreted_by_common_code': False,
        }

    def execute(self, _handle, request):
        return {'native_operation': request.get('execution_id')}

    def cancel(self, token):
        self.cancelled.append(token)
        return True

    def describe_result(self, _token):
        return {
            'result_kind': self.profile.result_kind,
            'schema': {'fields': [{'name': 'value'}]},
            'complete': True,
            'payload': [{'value': self.profile.engine_id}],
        }

    def describe_security(self, _request):
        return {
            'resource_id': f'{self.profile.engine_id}:security:current',
            'display_name': 'Current authorization',
            'authority_path': [self.profile.engine_id, 'security', 'current'],
            'generation': 'security-generation-one',
            'native': {'authorization_model': self.profile.engine_id},
        }

    def close(self):
        self.closed = True


def provider(provider_type, profile):
    return provider_type(context(profile), Permissions(), Client(profile))


class ActualEnginePilotContractTests(unittest.TestCase):

    def test_builtins_contain_only_preserved_and_live_qualified_profiles(self):
        class Registry:
            def __init__(self):
                self.packages = []

            def register_package(self, manifest, module_name):
                value = (manifest, module_name)
                self.packages.append(value)
                return value

        registry = Registry()
        registrations = register_builtin_providers(registry)
        self.assertEqual(26, len(registrations))
        self.assertEqual(
            {
                'postgresql-native', 'mysql-native', 'mariadb-native',
                'duckdb-native', 'firebird-native', 'mongodb-native',
                'neo4j-native', 'cassandra-native', 'redis-native',
                'xtdb-native', 'clickhouse-native', 'sqlite-native',
                'influxdb-native', 'milvus-native', 'opensearch-native',
                'opensearch-sql-ppl',
                'apache-ignite-native', 'cockroachdb-native',
                'dolt-native', 'foundationdb-native', 'immudb-native',
                'tidb-native',
                'tikv-native', 'vitess-native', 'yugabytedb-native',
                'yugabytedb-ycql',
            },
            {
                manifest['identity']['profile_id']
                for manifest, _module_name in registry.packages
            },
        )
        for manifest, _module_name in registry.packages:
            self.assertTrue(manifest['enabled'])
            self.assertTrue(manifest['production_registration'])

    def test_repository_policy_gate_passes(self):
        result = evaluate(ROOT)
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(16, result['pilot_profiles'])
        self.assertEqual(0, result['donor_manifests_verified'])

    def test_only_live_qualified_manifests_are_activatable(self):
        schema = load_contract_schema()
        record = json.loads(RECORD_PATH.read_text(encoding='utf-8'))
        for row in record['pilots'][1:]:
            manifest = json.loads(
                (PROVIDER_ROOT / row['manifest']).read_text(encoding='utf-8')
            )
            validated = validate_contract('ProviderManifest', manifest, schema)
            qualified = row['engine_id'] in {
                'mysql', 'mariadb', 'mongodb', 'duckdb', 'firebird',
                'neo4j', 'cassandra', 'redis', 'xtdb', 'clickhouse',
                'sqlite', 'influxdb', 'milvus', 'opensearch',
                'opensearch_sql_ppl',
            }
            self.assertEqual(qualified, validated['enabled'])
            self.assertEqual(
                qualified, validated['production_registration']
            )
            self.assertEqual(
                'experimental' if qualified else 'deferred',
                validated['support_state'],
            )

    def test_pilot_order_reference_profiles_and_live_status_are_recorded(self):
        record = json.loads(RECORD_PATH.read_text(encoding='utf-8'))
        self.assertEqual(
            [
                ('postgresql', '18.3', 1), ('mysql', '9.7.0', 2),
                ('mariadb', '12.2.2', 2), ('mongodb', '8.2.6', 3),
                ('neo4j', '2026.04.0', 4),
                ('cassandra', '5.0.8', 5),
                ('redis', '8.6.2', 6),
                ('xtdb', '2.1.0', 7),
                ('clickhouse', '25.12.10.7-stable', 5),
                ('influxdb', '3.9.0', 6),
                ('milvus', '2.6.5', 7),
                ('opensearch', '3.6.0', 8),
                ('opensearch_sql_ppl', '3.6.0-sql-ppl', 9),
                ('duckdb', '1.5.2', 6),
                ('firebird', '5.0.4', 7),
                ('sqlite', '3.53.0', 8),
            ],
            [(row['engine_id'], row['exact_profile'], row['order'])
             for row in record['pilots']],
        )
        live_status = {
            row['engine_id']: row['live_suite']
            for row in record['pilots']
        }
        self.assertEqual('passed', live_status.pop('mysql'))
        self.assertEqual('passed', live_status.pop('mariadb'))
        self.assertEqual('passed', live_status.pop('mongodb'))
        self.assertEqual('passed', live_status.pop('neo4j'))
        self.assertEqual('passed', live_status.pop('cassandra'))
        self.assertEqual('passed', live_status.pop('duckdb'))
        self.assertEqual('passed', live_status.pop('firebird'))
        self.assertEqual('passed', live_status.pop('sqlite'))
        self.assertEqual('passed', live_status.pop('redis'))
        self.assertEqual('passed', live_status.pop('xtdb'))
        self.assertEqual('passed', live_status.pop('clickhouse'))
        self.assertEqual('passed', live_status.pop('influxdb'))
        self.assertEqual('passed', live_status.pop('milvus'))
        self.assertEqual('passed', live_status.pop('opensearch'))
        self.assertEqual('passed', live_status.pop('opensearch_sql_ppl'))
        self.assertTrue(all(
            status == 'not_run' for status in live_status.values()
        ))
        neo4j = next(
            row for row in record['pilots']
            if row['engine_id'] == 'neo4j'
        )
        self.assertEqual(
            'community_plus_gds_plus_enterprise_cluster_2026.04.0',
            neo4j['qualification_scope'],
        )
        self.assertEqual(
            'full_graph_11_of_11_concepts_31_of_31_operations',
            neo4j['object_experience_state'],
        )

    def test_mysql_and_mariadb_share_wire_but_not_semantics(self):
        self.assertEqual(
            MYSQL_PROFILE.protocol_id, MARIADB_PROFILE.protocol_id
        )
        self.assertNotEqual(
            MYSQL_PROFILE.language_profile, MARIADB_PROFILE.language_profile
        )
        self.assertNotEqual(
            MYSQL_PROFILE.transaction_model,
            MARIADB_PROFILE.transaction_model,
        )
        self.assertNotEqual(
            MYSQL_PROFILE.admin_tools, MARIADB_PROFILE.admin_tools
        )

    def test_exact_runtime_identity_succeeds_for_every_provider(self):
        for provider_type, profile in PILOTS:
            instance = provider(provider_type, profile)
            discovered = instance.discover_endpoint({
                'route': {'endpoint_uri': 'opaque-reference'}
            })
            self.assertEqual(
                profile.exact_version,
                discovered['verified_runtime']['version'],
            )
            self.assertEqual(
                'verified',
                discovered['verified_runtime']['verification_state'],
            )

    def test_semantic_compiler_admission_is_explicit_for_every_provider(self):
        admitted = {
            'mysql', 'mariadb', 'duckdb', 'firebird', 'sqlite',
            'xtdb', 'clickhouse', 'influxdb', 'cockroachdb', 'dolt',
            'tidb', 'vitess', 'yugabytedb',
        }
        observed = set()
        for provider_type, profile in PILOTS:
            instance = provider(provider_type, profile)
            descriptor = instance.semantic_model_descriptor()
            self.assertEqual(profile.engine_id, descriptor['engine_id'])
            self.assertEqual(profile.model_family, descriptor['model_family'])
            if profile.engine_id in admitted:
                observed.add(profile.engine_id)
                self.assertTrue(descriptor['execution_available'])
                compiled = instance.compile_semantic_query(
                    semantic_model(profile.engine_id), semantic_query()
                )
                self.assertEqual(
                    profile.semantic_sql_dialect['language_profile'],
                    compiled['language_profile'],
                )
                self.assertIn('COUNT(*)', compiled['source'])
            else:
                self.assertFalse(descriptor['execution_available'])
                self.assertIsNotNone(descriptor['reason'])
                with self.assertRaises(SemanticCompilationUnavailable):
                    instance.compile_semantic_query(
                        semantic_model(profile.engine_id), semantic_query()
                    )
        self.assertEqual(admitted, observed)

    def test_wrong_runtime_engine_version_or_protocol_fails_closed(self):
        for field in ('engine_id', 'version', 'protocol_id'):
            client = Client(MONGODB)
            identity = client.runtime_identity

            def mismatched(*_args, identity=identity, field=field):
                return {**identity(), field: 'wrong'}

            client.runtime_identity = mismatched
            instance = MongoDBPilotProvider(
                context(MONGODB), Permissions(), client
            )
            with self.assertRaises(RuntimeIdentityError):
                instance.discover_endpoint({'route': {'uri': 'opaque'}})

    def test_provider_declared_minimum_version_accepts_newer_redis(self):
        self.assertEqual('>=8.6.0', REDIS.version_requirement)
        client = Client(REDIS)
        original = client.runtime_identity

        def identity(version):
            return {**original(), 'version': version}

        client.runtime_identity = lambda: identity('8.10.1')
        instance = RedisPilotProvider(
            context(REDIS), Permissions(), client
        )
        discovered = instance.discover_endpoint({
            'route': {'endpoint_uri': 'opaque-reference'}
        })
        self.assertEqual('8.10.1', discovered['verified_runtime']['version'])

        client.runtime_identity = lambda: identity('8.5.9')
        with self.assertRaises(RuntimeIdentityError):
            instance.discover_endpoint({
                'route': {'endpoint_uri': 'opaque-reference'}
            })

    def test_inline_credentials_and_absent_client_are_refused(self):
        instance = provider(MySQLPilotProvider, MYSQL_PROFILE)
        diagnostic = instance.validate_endpoint({
            'route': {'endpoint_uri': 'opaque', 'password': 'do-not-copy'}
        })
        self.assertEqual(
            'CDE_ACTUAL_INLINE_CREDENTIAL_FORBIDDEN', diagnostic['code']
        )
        with self.assertRaisesRegex(PilotProviderError, 'client adapter'):
            MySQLPilotProvider(context(MYSQL_PROFILE), Permissions(), None)

    def test_resource_and_language_api_suites_are_provider_owned(self):
        for provider_type, profile in PILOTS:
            instance = provider(provider_type, profile)
            resources = instance.list_resources({'resource_id': 'endpoint'})
            languages = instance.describe_language({})
            inspected = instance.inspect_resource(resources[0])
            self.assertEqual(
                profile.resource_kinds[0], inspected['resource_kind']
            )
            self.assertEqual('language-profile', languages[0]['resource_kind'])
            self.assertEqual(
                profile.model_family, resources[0]['model_family']
            )

    def test_result_and_transaction_suites_remain_provider_opaque(self):
        for provider_type, profile in PILOTS:
            instance = provider(provider_type, profile)
            session = instance.open_session({'route': {'route_id': 'direct'}})
            transaction = instance.describe_transaction(session)
            operation = instance.execute({
                'session_id': session['session_id'],
                'execution_id': f'{profile.engine_id}-execution',
                'source': 'provider-native-source',
            })
            result = instance.describe_result(operation)
            self.assertEqual(profile.transaction_model,
                             transaction['transaction_model'])
            self.assertFalse(transaction['provider_payload'][
                'finality_interpreted_by_common_code'
            ])
            self.assertEqual(profile.result_kind, result['result_kind'])
            self.assertTrue(instance.get_operation(operation)['terminal'])

    def test_admin_security_and_fault_suites_are_redacted(self):
        for provider_type, profile in PILOTS:
            instance = provider(provider_type, profile)
            tools = instance.list_tools({})
            security = instance.describe_security({'resource_id': 'endpoint'})
            fault = instance.translate_diagnostic({
                'code': 'NATIVE_ERROR', 'message': 'password=secret',
                'details': {'secret': 'secret'},
                'exception_type': 'NativeError',
                'retryable': True,
            })
            self.assertEqual(len(profile.admin_tools), len(tools))
            self.assertEqual('security-descriptor', security['resource_kind'])
            self.assertNotIn('secret', json.dumps(fault))
            self.assertEqual(
                {'exception_type': 'NativeError'}, fault['details']
            )

    def test_cancellation_never_claims_transaction_finality(self):
        instance = provider(Neo4jPilotProvider, NEO4J)
        session = instance.open_session({'route': {'route_id': 'direct'}})
        operation = instance.execute({
            'session_id': session['session_id'], 'execution_id': 'one'
        })
        cancelled = instance.cancel(operation)
        self.assertFalse(cancelled['terminal'])
        self.assertEqual(
            'pending-provider-observation',
            cancelled['provider_receipt']['outcome'],
        )

    def test_endpoint_state_and_faults_do_not_cross_providers(self):
        first = provider(MySQLPilotProvider, MYSQL_PROFILE)
        second = provider(MariaDBPilotProvider, MARIADB_PROFILE)
        first_session = first.open_session({'route': {'route_id': 'one'}})
        with self.assertRaises(PilotProviderError):
            second.describe_transaction(first_session)

    def test_close_drops_handles_and_closes_only_own_client(self):
        instance = provider(ClickHousePilotProvider, CLICKHOUSE)
        client = instance.client
        session = instance.open_session({'route': {'route_id': 'one'}})
        instance.close()
        self.assertTrue(client.closed)
        with self.assertRaises(PilotProviderError):
            instance.describe_transaction(session)


if __name__ == '__main__':
    unittest.main()
