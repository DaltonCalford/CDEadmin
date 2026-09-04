##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Relational provider tests for CDE-REL-000 through CDE-REL-040."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
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

from pgadmin.cdeadmin.core import EndpointContext  # noqa: E402
from pgadmin.cdeadmin.security import SecretLease  # noqa: E402
from pgadmin.cdeadmin.providers.duckdb.provider import (  # noqa: E402
    PROFILE as DUCKDB,
    _initialize_connection as initialize_duckdb_connection,
    _route_arguments as duckdb_route_arguments,
    _version as duckdb_version,
)
from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    PROFILE as FIREBIRD,
    _initialize_connection as initialize_firebird_connection,
    _route_arguments as firebird_route_arguments,
    _version as firebird_version,
)
from pgadmin.cdeadmin.providers.mysql_family.provider import (  # noqa: E402
    MariaDBDBAPIClient,
    MARIADB_PROFILE,
    MYSQL_PROFILE,
    _MariaDBConnectorFacade,
    _initialize_connection as initialize_mysql_connection,
    _resources as mysql_resources,
    _route_arguments as mysql_route_arguments,
    _version as mysql_version,
)
from pgadmin.cdeadmin.providers.sqlite.provider import (  # noqa: E402
    PROFILE as SQLITE,
    SQLiteProvider,
    _initialize_connection as initialize_sqlite_connection,
    _resources as sqlite_resources,
    _route_arguments as sqlite_route_arguments,
    _security as sqlite_security,
)
from pgadmin.cdeadmin.sdk import (  # noqa: E402
    PilotProfile,
    RelationalClientConfig,
    RelationalClientError,
    RelationalDBAPIClient,
    RelationalDependencyError,
    RuntimeIdentityError,
    load_optional_module,
)


PROVIDER_ROOT = WEB / 'pgadmin/cdeadmin/providers'
SELECTION_PATH = (
    WEB / 'pgadmin/cdeadmin/transports/protocol_client_selections.json'
)


class Permissions:
    def __init__(self):
        self.calls = []

    def require(self, permission, scope='endpoint'):
        self.calls.append((permission, scope))


def context(profile, label='one'):
    endpoint_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f'relational:{profile.engine_id}:{label}'
    )

    def child(purpose):
        return str(uuid.uuid5(endpoint_id, purpose))

    return EndpointContext(
        endpoint_id=str(endpoint_id), mode='legacy_native',
        experience_family=profile.engine_id,
        provider_id=profile.provider_id, provider_version='0.1.0',
        profile_id=profile.profile_id, profile_version=profile.exact_version,
        target_adapter_id=f'{profile.protocol_id}-client',
        target_adapter_version='dbapi', pool_namespace=child('pool'),
        session_namespace=child('session'), cache_namespace=child('cache'),
        diagnostic_namespace=child('diagnostic'),
        effective_permissions=frozenset({
            'network', 'secret_read', 'data_read', 'data_write',
            'administer', 'execute', 'embedded_runtime', 'filesystem',
        }),
    )


def installed_sqlite_profile():
    return PilotProfile(
        'org.cdeadmin.sqlite.test', 'sqlite-installed', 'sqlite', 'SQLite',
        sqlite3.sqlite_version, 'embedded_sqlite', 'relational',
        'sqlite-sql', 'SQLite SQL', 'sqlite-native-transaction', 'tabular',
        SQLITE.resource_kinds, SQLITE.admin_tools,
        SQLITE.required_permissions,
    )


def sqlite_client(profile):
    return RelationalDBAPIClient(RelationalClientConfig(
        profile=profile,
        module_name='sqlite3',
        version_query='SELECT sqlite_version()',
        connect_arguments=sqlite_route_arguments,
        metadata_reader=sqlite_resources,
        security_reader=sqlite_security,
    ), sqlite3)


class RelationalInventoryTests(unittest.TestCase):

    def test_duckdb_attachment_initializer_is_contained_and_idempotent(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest('DuckDB driver is not installed')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attached = root / 'archive.duckdb'
            duckdb.connect(str(attached)).close()
            connection = duckdb.connect(str(root / 'main.duckdb'))
            route = {
                'database': str(root / 'main.duckdb'),
                'filesystem_root': str(root),
                'attached_databases': [{
                    'name': 'archive', 'database': str(attached),
                    'read_only': True,
                }],
            }
            try:
                initialize_duckdb_connection(connection, route)
                initialize_duckdb_connection(connection, route)
                names = {
                    row[0] for row in connection.execute(
                        'SELECT database_name FROM duckdb_databases()'
                    ).fetchall()
                }
                self.assertIn('archive', names)
            finally:
                connection.close()

    def test_sqlite_attachment_initializer_is_contained_and_discoverable(
            self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            connection = sqlite3.connect(root / 'main.sqlite')
            try:
                initialize_sqlite_connection(connection, {
                    'database': str(root / 'main.sqlite'),
                    'filesystem_root': str(root),
                    'attached_databases': [{
                        'name': 'archive',
                        'database': str(root / 'archive.sqlite'),
                    }],
                })
                resources = sqlite_resources(connection, {})
                attached = next(
                    item for item in resources
                    if item['resource_kind'] == 'attached-database'
                )
                self.assertEqual('archive', attached['display_name'])
                self.assertTrue(any(
                    item['resource_kind'] == 'extension'
                    for item in resources
                ))
            finally:
                connection.close()

    def test_sqlite_attachment_initializer_rejects_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'approved'
            root.mkdir()
            connection = sqlite3.connect(root / 'main.sqlite')
            try:
                with self.assertRaisesRegex(
                    RelationalClientError, 'escapes',
                ):
                    initialize_sqlite_connection(connection, {
                        'database': str(root / 'main.sqlite'),
                        'filesystem_root': str(root),
                        'attached_databases': [{
                            'name': 'outside',
                            'database': str(Path(temporary) / 'outside.db'),
                        }],
                    })
            finally:
                connection.close()

    def test_mysql_plugin_discovery_uses_native_server_catalog(self):
        class Cursor:
            source = ''

            def execute(self, source):
                self.source = source

            def fetchall(self):
                if 'information_schema.TABLES' in self.source:
                    return [('app', 'widgets', 'BASE TABLE')]
                if self.source == 'SHOW PLUGINS':
                    return [(
                        'auth_example', 'ACTIVE', 'AUTHENTICATION',
                        'auth_example.so', 'GPL',
                    )]
                return []

            @staticmethod
            def close():
                return None

        class Connection:
            @staticmethod
            def cursor():
                return Cursor()

        resources = mysql_resources(
            Connection(), {'capability_generation': 'generation-one'}
        )
        plugin = next(
            item for item in resources
            if item['resource_kind'] == 'plugin'
        )
        self.assertEqual('auth_example', plugin['display_name'])
        self.assertEqual('auth_example.so', plugin['native']['library'])
        self.assertEqual('generation-one', plugin['generation'])

    def test_six_relational_profiles_are_present_and_distinct(self):
        profiles = (MYSQL_PROFILE, MARIADB_PROFILE, DUCKDB, FIREBIRD, SQLITE)
        observed = {profile.engine_id: profile.exact_version
                    for profile in profiles}
        observed['postgresql'] = '18.3'
        self.assertEqual({
            'postgresql': '18.3', 'mysql': '9.7.0',
            'mariadb': '12.2.2', 'duckdb': '1.5.2',
            'firebird': '5.0.4', 'sqlite': '3.53.0',
        }, observed)
        self.assertEqual('relational', DUCKDB.model_family)

    def test_only_live_qualified_manifests_are_activated(self):
        qualified_paths = (
            'mysql_family/mysql_provider_manifest.json',
            'mysql_family/mariadb_provider_manifest.json',
            'duckdb/provider_manifest.json',
            'firebird/provider_manifest.json',
            'sqlite/provider_manifest.json',
        )
        deferred_paths = ()
        for relative in qualified_paths:
            manifest = json.loads(
                (PROVIDER_ROOT / relative).read_text(encoding='utf-8')
            )
            self.assertTrue(manifest['enabled'])
            self.assertTrue(manifest['production_registration'])
            self.assertEqual('experimental', manifest['support_state'])
            if 'network' in manifest['required_permissions']:
                self.assertIn(
                    'secret_read', manifest['required_permissions']
                )
            else:
                self.assertEqual(
                    ['embedded_runtime', 'filesystem'],
                    manifest['required_permissions'],
                )
        for relative in deferred_paths:
            manifest = json.loads(
                (PROVIDER_ROOT / relative).read_text(encoding='utf-8')
            )
            self.assertFalse(manifest['enabled'])
            self.assertFalse(manifest['production_registration'])
            self.assertEqual('deferred', manifest['support_state'])

    def test_product_profile_catalog_has_no_implementation_selector(self):
        selections = json.loads(SELECTION_PATH.read_text(encoding='utf-8'))
        for profile in selections['engine_profiles']:
            self.assertNotIn('scratchbird_emulation', profile)
            self.assertNotIn('server_implementation', profile)

    def test_optional_dependency_failure_is_actionable_and_redacted(self):
        with self.assertRaisesRegex(
            RelationalDependencyError,
            "relational client dependency 'not_a_real_cde_driver'",
        ):
            load_optional_module('not_a_real_cde_driver')

    def test_engine_version_normalizers_accept_advertised_versions(self):
        self.assertEqual('9.7.0', mysql_version(('9.7.0-commercial',)))
        self.assertEqual(
            '12.2.2', mysql_version(('12.2.2-MariaDB',))
        )
        self.assertEqual('1.5.2', duckdb_version(('v1.5.2',)))
        self.assertEqual(
            '5.0.4', firebird_version(('WI-V5.0.4.1702 Firebird',))
        )

    def test_route_mappers_drop_non_connection_and_credential_fields(self):
        route = {
            'host': 'db.example', 'port': 3306, 'user': 'operator',
            'database': 'inventory', 'connection_timeout': 7,
            'password': 'must-not-pass', 'server_implementation': 'ignored',
        }
        mysql = mysql_route_arguments(route, MYSQL_PROFILE)
        mariadb = mysql_route_arguments(route, MARIADB_PROFILE)
        self.assertNotIn('password', mysql)
        self.assertNotIn('server_implementation', mysql)
        self.assertEqual(7, mysql['connection_timeout'])
        self.assertNotIn('connection_timeout', mariadb)
        self.assertEqual(7, mariadb['connect_timeout'])
        with tempfile.TemporaryDirectory() as temporary:
            database = str(Path(temporary) / 'one.duckdb')
            self.assertEqual(
                {'database': database, 'read_only': True},
                duckdb_route_arguments({
                    'database': database, 'filesystem_root': temporary,
                    'read_only': True, 'password': 'must-not-pass',
                }),
            )
        self.assertEqual(
            {'database': 'db.example:inventory', 'user': 'operator'},
            firebird_route_arguments({
                'database': 'db.example:inventory', 'user': 'operator',
                'password': 'must-not-pass',
            }),
        )

    def test_firebird_route_maps_trusted_auth_and_dpb_configuration(self):
        import firebird.driver as firebird_module

        route = firebird_route_arguments({
            'route_id': 'firebird-route-configuration-test',
            'host': 'firebird.example', 'port': 3050,
            'database': '/srv/firebird/inventory.fdb',
            'trusted_auth': True, 'protocol': 'INET4', 'timeout': 12,
            'dummy_packet_interval': 30,
            'wire_config': 'WireCrypt=Required',
            'dbkey_scope': 'ATTACHMENT',
            'auth_plugin_list': 'Win_Sspi,Srp256',
        }, firebird_module)
        self.assertTrue(route['database'].startswith('cde_database_'))
        self.assertEqual(
            firebird_module.DBKeyScope.ATTACHMENT, route['dbkey_scope']
        )
        self.assertEqual('Win_Sspi,Srp256', route['auth_plugin_list'])
        config = firebird_module.driver_config.get_database(
            route['database']
        )
        self.assertTrue(config.trusted_auth.value)
        self.assertEqual(12, config.timeout.value)
        self.assertEqual('WireCrypt=Required', config.config.value)

    def test_firebird_transaction_defaults_use_typed_driver_tpb(self):
        import firebird.driver as firebird_module

        transaction = SimpleNamespace(default_tpb=None)
        connection = SimpleNamespace(
            default_tpb=None, main_transaction=transaction
        )
        initialize_firebird_connection(connection, {
            'transaction_isolation': 'READ_COMMITTED_READ_CONSISTENCY',
            'transaction_access': 'READ',
            'transaction_lock_timeout': 15,
        }, firebird_module)
        self.assertIsInstance(connection.default_tpb, bytes)
        self.assertEqual(
            connection.default_tpb, transaction.default_tpb
        )

    def test_mysql_transaction_isolation_is_allowlisted_and_initialized(self):
        statements = []
        cursor = SimpleNamespace(
            execute=statements.append, close=lambda: None
        )
        initialize_mysql_connection(
            SimpleNamespace(cursor=lambda: cursor),
            {'transaction_isolation': 'SERIALIZABLE'},
        )
        self.assertEqual([
            'SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE'
        ], statements)
        with self.assertRaisesRegex(
            RelationalClientError, 'transaction isolation is invalid'
        ):
            initialize_mysql_connection(
                SimpleNamespace(cursor=lambda: cursor),
                {'transaction_isolation': 'INJECTED; DROP DATABASE'},
            )

    def test_embedded_routes_refuse_unapproved_or_escaping_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'approved'
            root.mkdir()
            outside = Path(temporary) / 'outside.sqlite'
            with self.assertRaisesRegex(
                RelationalClientError, 'escapes the approved filesystem root'
            ):
                sqlite_route_arguments({
                    'database': str(outside),
                    'filesystem_root': str(root),
                })
            with self.assertRaisesRegex(
                RelationalClientError, 'in-memory database is not approved'
            ):
                sqlite_route_arguments({'database': ':memory:'})
            with self.assertRaisesRegex(
                RelationalClientError, 'URI routes are unavailable'
            ):
                sqlite_route_arguments({
                    'database': str(root / 'db.sqlite'),
                    'filesystem_root': str(root), 'uri': True,
                })

    def test_sqlite_safe_uri_and_session_defaults_are_forwarded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / 'inventory db.sqlite'
            values = sqlite_route_arguments({
                'database': str(database), 'filesystem_root': str(root),
                'timeout': 0.25, 'uri_mode': 'ro',
                'uri_cache': 'private', 'uri_immutable': True,
                'detect_types': 'both', 'isolation_level': 'immediate',
                'cached_statements': 512,
            })
        self.assertTrue(values['uri'])
        self.assertTrue(values['database'].startswith('file:/'))
        self.assertIn('mode=ro', values['database'])
        self.assertIn('immutable=1', values['database'])
        self.assertEqual(0.25, values['timeout'])
        self.assertEqual(3, values['detect_types'])
        self.assertEqual('IMMEDIATE', values['isolation_level'])
        self.assertEqual(512, values['cached_statements'])

    def test_duckdb_read_only_and_named_scalar_config_are_forwarded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = duckdb_route_arguments({
                'database': str(root / 'warehouse.duckdb'),
                'filesystem_root': str(root), 'read_only': True,
                'config': {'threads': 4, 'memory_limit': '2GB'},
            })
            self.assertTrue(values['read_only'])
            self.assertEqual(4, values['config']['threads'])
            with self.assertRaisesRegex(
                RelationalClientError, 'named scalar options'
            ):
                duckdb_route_arguments({
                    'database': str(root / 'warehouse.duckdb'),
                    'filesystem_root': str(root),
                    'config': {'nested': {'not': 'admitted'}},
                })

    def test_mariadb_pool_arguments_use_native_connection_pool(self):
        observed = {}

        class Pool:
            def __init__(self, **kwargs):
                observed['pool_options'] = kwargs
                self.closed = False

            def get_connection(self):
                observed['checkout'] = True
                return SimpleNamespace(close=lambda: None)

            def close(self):
                self.closed = True
                observed['closed'] = True

        module = SimpleNamespace(
            connect=lambda **kwargs: observed.setdefault('direct', kwargs),
            ConnectionPool=Pool,
        )
        facade = _MariaDBConnectorFacade(module, 'pool-namespace')
        connection = facade(
            host='db.example', user='operator', password='secret-canary',
            pool_name='cde_test_pool', pool_size=7,
            pool_reset_connection=False, pool_validation_interval=900,
        )
        self.assertIsNotNone(connection)
        self.assertNotIn('direct', observed)
        self.assertTrue(observed['checkout'])
        self.assertEqual(7, observed['pool_options']['pool_size'])
        self.assertEqual(
            900, observed['pool_options']['pool_validation_interval']
        )
        self.assertEqual('db.example', observed['pool_options']['host'])
        facade.close()
        self.assertTrue(observed['closed'])

    def test_mariadb_client_closes_native_pool_after_checked_out_handle(self):
        events = []

        class Connection:
            def close(self):
                events.append('connection')

        class Pool:
            def __init__(self, **_kwargs):
                pass

            def get_connection(self):
                return Connection()

            def close(self):
                events.append('pool')

        module = SimpleNamespace(
            connect=lambda **_kwargs: Connection(), ConnectionPool=Pool
        )
        client = MariaDBDBAPIClient(RelationalClientConfig(
            profile=MARIADB_PROFILE,
            module_name='test.mariadb.dbapi',
            version_query='SELECT VERSION()',
            connect_arguments=lambda route: dict(route),
            metadata_reader=lambda *_args: [],
        ), 'pool-namespace', module)
        client.open_session({'route': {
            'host': 'db.example', 'pool_name': 'cde_test_pool',
            'pool_size': 2,
        }})
        client.close()
        self.assertEqual(['connection', 'pool'], events)


class RelationalDBAPIClientTests(unittest.TestCase):

    def setUp(self):
        self.profile = installed_sqlite_profile()
        self.client = sqlite_client(self.profile)
        self.provider = SQLiteProvider(
            context(self.profile), Permissions(), self.client
        )
        self.provider.profile = self.profile

    def tearDown(self):
        self.provider.close()

    def test_discovery_and_session_use_advertised_profile_only(self):
        route = {
            'database': ':memory:', 'allow_memory': True,
            'route_id': 'local',
        }
        discovered = self.provider.discover_endpoint({'route': route})
        self.assertEqual(
            sqlite3.sqlite_version,
            discovered['verified_runtime']['version'],
        )
        first = SQLiteProvider(
            context(self.profile, 'reference'), Permissions(),
            sqlite_client(self.profile),
        )
        second = SQLiteProvider(
            context(self.profile, 'compatible'), Permissions(),
            sqlite_client(self.profile),
        )
        first.profile = self.profile
        second.profile = self.profile
        try:
            first_session = first.open_session({'route': route})
            second_session = second.open_session({'route': route})
            self.assertEqual(
                first_session['language_profile'],
                second_session['language_profile'],
            )
            self.assertNotIn('server_implementation', first_session)
            self.assertNotIn('emulation', json.dumps(first_session).lower())
        finally:
            first.close()
            second.close()

    def test_query_result_and_transaction_are_provider_owned(self):
        session = self.provider.open_session({
            'route': {
                'database': ':memory:', 'allow_memory': True,
                'route_id': 'local',
            }
        })
        transaction = self.provider.describe_transaction(session)
        self.assertTrue(transaction['provider_payload'][
            'driver_observation_only'
        ])
        self.assertFalse(transaction['provider_payload'][
            'finality_interpreted_by_common_code'
        ])
        operation = self.provider.execute({
            'session_id': session['session_id'],
            'execution_id': 'sqlite-query-one',
            'source': 'SELECT ? AS value',
            'parameters': [42],
        })
        result = self.provider.describe_result(operation)
        payload = result['extensions']['sqlite']['payload']
        self.assertEqual([[42]], [list(row) for row in payload['rows']])
        self.assertTrue(result['complete'])

    def test_metadata_and_cleanup_are_bounded(self):
        request = {
            'route': {'database': ':memory:', 'allow_memory': True},
            'capability_generation': 'test-generation',
        }
        resources = self.provider.list_resources(request)
        self.assertEqual('database', resources[0]['resource_kind'])
        self.assertEqual('test-generation', resources[0]['generation'])
        security = self.provider.describe_security(request)
        self.assertEqual('security-descriptor', security['resource_kind'])
        self.assertEqual('test-generation', security['generation'])
        self.provider.close()
        self.assertEqual([], self.client._connections)
        self.assertEqual([], self.client._tokens)

    def test_target_sqlite_profile_refuses_installed_version_mismatch(self):
        if sqlite3.sqlite_version == SQLITE.exact_version:
            self.skipTest('installed SQLite now matches the target profile')
        provider = SQLiteProvider(
            context(SQLITE), Permissions(), sqlite_client(SQLITE)
        )
        try:
            with self.assertRaises(RuntimeIdentityError):
                provider.open_session({
                    'route': {
                        'database': ':memory:', 'allow_memory': True,
                        'route_id': 'local',
                    }
                })
        finally:
            provider.close()

    def test_secret_reference_is_bound_only_during_connector_call(self):
        leases = []
        observed = {}

        class Connection:
            closed = False

            def close(self):
                self.closed = True

        def acquire(reference, principal, purpose, expected_kind):
            observed['acquisition'] = (
                reference, principal, purpose, expected_kind
            )
            lease = SecretLease(b'connector-password-canary')
            leases.append(lease)
            return lease

        def connect(**kwargs):
            observed['password_valid'] = (
                kwargs.pop('password') == 'connector-password-canary'
            )
            observed['connector_keys'] = frozenset(kwargs)
            return Connection()

        client = RelationalDBAPIClient(RelationalClientConfig(
            profile=self.profile,
            module_name='test.secret.dbapi',
            version_query='SELECT sqlite_version()',
            connect_arguments=lambda route: dict(route),
            metadata_reader=lambda *_args: [],
            credential_argument='password',
            secret_acquirer=acquire,
        ), SimpleNamespace(connect=connect))
        connection = client.open_session({'route': {
            'database': ':memory:',
            'credential_reference_id': 'reference-one',
            'principal_reference': 'principal-one',
        }})
        try:
            self.assertTrue(observed['password_valid'])
            self.assertEqual(frozenset({'database'}), observed[
                'connector_keys'
            ])
            self.assertEqual(
                ('reference-one', 'principal-one', 'connect',
                 'database_password'),
                observed['acquisition'],
            )
            self.assertTrue(leases[0].closed)
            self.assertEqual({0}, set(leases[0]._buffer))
        finally:
            client.close()
        self.assertTrue(connection.closed)

    def test_secret_binding_failures_are_closed_and_redacted(self):
        leases = []

        def acquire(*_args):
            lease = SecretLease(b'failure-password-canary')
            leases.append(lease)
            return lease

        def connect(**kwargs):
            raise RuntimeError(f"password={kwargs.get('password')}")

        client = RelationalDBAPIClient(RelationalClientConfig(
            profile=self.profile,
            module_name='test.secret.failure.dbapi',
            version_query='SELECT sqlite_version()',
            connect_arguments=lambda route: dict(route),
            metadata_reader=lambda *_args: [],
            credential_argument='password',
            secret_acquirer=acquire,
        ), SimpleNamespace(connect=connect))
        with self.assertRaises(RelationalClientError) as raised:
            client.open_session({'route': {
                'credential_reference_id': 'reference-one',
                'principal_reference': 'principal-one',
            }})
        self.assertNotIn('failure-password-canary', str(raised.exception))
        self.assertTrue(leases[0].closed)
        self.assertEqual({0}, set(leases[0]._buffer))
        with self.assertRaisesRegex(
            RelationalClientError, 'requires a principal reference'
        ):
            client.open_session({'route': {
                'credential_reference_id': 'reference-one',
            }})

    def test_multiple_typed_credentials_bind_to_distinct_arguments(self):
        observed = {'acquisitions': []}
        secrets = {
            'primary-reference': b'primary-canary',
            'second-reference': b'second-canary',
            'key-reference': b'key-canary',
        }

        def acquire(reference, principal, purpose, expected_kind):
            observed['acquisitions'].append((
                reference, principal, purpose, expected_kind
            ))
            return SecretLease(secrets[reference])

        def connect(**kwargs):
            observed['arguments'] = kwargs
            return SimpleNamespace(close=lambda: None)

        client = RelationalDBAPIClient(RelationalClientConfig(
            profile=self.profile,
            module_name='test.multi.secret.dbapi',
            version_query='SELECT sqlite_version()',
            connect_arguments=lambda route: dict(route),
            metadata_reader=lambda *_args: [],
            credential_arguments={
                'database_password': 'password',
                'database_password_2': 'password2',
                'tls_private_key_password': 'sslpassword',
            },
            secret_acquirer=acquire,
        ), SimpleNamespace(connect=connect))
        client.open_session({'route': {
            'database': 'qualification',
            'credential_reference_id': 'primary-reference',
            'credential_kind': 'database_password',
            'credential_references': {
                'database_password': 'primary-reference',
                'database_password_2': 'second-reference',
                'tls_private_key_password': 'key-reference',
            },
            'credential_kinds': [
                'database_password', 'database_password_2',
                'tls_private_key_password',
            ],
            'principal_reference': 'principal-one',
        }})
        self.assertEqual('primary-canary', observed['arguments']['password'])
        self.assertEqual('second-canary', observed['arguments']['password2'])
        self.assertEqual('key-canary', observed['arguments']['sslpassword'])
        self.assertEqual(3, len(observed['acquisitions']))

    def test_provider_tool_credentials_are_not_forwarded_to_connector(self):
        observed = {'acquisitions': []}

        def acquire(reference, principal, purpose, expected_kind):
            observed['acquisitions'].append((
                reference, principal, purpose, expected_kind
            ))
            return SecretLease(b'database-password-canary')

        def connect(**kwargs):
            observed['arguments'] = kwargs
            return SimpleNamespace(close=lambda: None)

        client = RelationalDBAPIClient(RelationalClientConfig(
            profile=self.profile,
            module_name='test.tool.secret.dbapi',
            version_query='SELECT sqlite_version()',
            connect_arguments=lambda route: dict(route),
            metadata_reader=lambda *_args: [],
            credential_arguments={'database_password': 'password'},
            tool_credential_kinds=frozenset({'provider_tool_password'}),
            secret_acquirer=acquire,
        ), SimpleNamespace(connect=connect))
        client.open_session({'route': {
            'database': 'qualification',
            'credential_references': {
                'database_password': 'database-reference',
                'provider_tool_password': 'tool-reference',
            },
            'principal_reference': 'principal-one',
        }})
        self.assertEqual(
            {'database': 'qualification',
             'password': 'database-password-canary'},
            observed['arguments'],
        )
        self.assertEqual([
            ('database-reference', 'principal-one', 'connect',
             'database_password'),
        ], observed['acquisitions'])


if __name__ == '__main__':
    unittest.main()
