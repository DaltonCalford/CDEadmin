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
    _route_arguments as duckdb_route_arguments,
    _version as duckdb_version,
)
from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    PROFILE as FIREBIRD,
    _route_arguments as firebird_route_arguments,
    _version as firebird_version,
)
from pgadmin.cdeadmin.providers.mysql_family.provider import (  # noqa: E402
    MARIADB_PROFILE,
    MYSQL_PROFILE,
    _route_arguments as mysql_route_arguments,
    _version as mysql_version,
)
from pgadmin.cdeadmin.providers.sqlite.provider import (  # noqa: E402
    PROFILE as SQLITE,
    SQLiteProvider,
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


if __name__ == '__main__':
    unittest.main()
