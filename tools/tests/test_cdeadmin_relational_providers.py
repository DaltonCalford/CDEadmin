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
import threading
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock


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
    _resources as duckdb_resources,
    _route_arguments as duckdb_route_arguments,
    _version as duckdb_version,
)
from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    PROFILE as FIREBIRD,
    _client_library_identity as firebird_client_library_identity,
    _firebird_service_operation,
    _initialize_connection as initialize_firebird_connection,
    _route_arguments as firebird_route_arguments,
    _version as firebird_version,
)
from pgadmin.cdeadmin.providers.mysql_family.provider import (  # noqa: E402
    MariaDBDBAPIClient,
    MARIADB_PROFILE,
    MySQLDBAPIClient,
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
    RelationalCredentialError,
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

    def test_duckdb_properties_use_exact_catalog_surfaces(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest('DuckDB driver is not installed')
        connection = duckdb.connect(':memory:')
        try:
            connection.execute(
                'CREATE TABLE parent(id INTEGER PRIMARY KEY, label VARCHAR)'
            )
            connection.execute(
                'CREATE TABLE child(id INTEGER, parent_id INTEGER '
                'REFERENCES parent(id))'
            )
            connection.execute(
                'CREATE INDEX child_parent ON child(parent_id)'
            )
            connection.execute(
                'CREATE VIEW shown AS SELECT * FROM child'
            )
            connection.execute('CREATE SEQUENCE sample_sequence START 4')
            resources = duckdb_resources(connection, {})
        finally:
            connection.close()
        child = next(
            item for item in resources
            if item['resource_kind'] == 'table' and
            item['display_name'] == 'child'
        )
        self.assertIn('CREATE TABLE', child['native']['ddl'])
        self.assertEqual(2, len(child['native']['columns']))
        self.assertEqual(1, len(child['native']['indexes']))
        self.assertTrue(any(
            item['constraint_type'] == 'FOREIGN KEY'
            for item in child['native']['constraints']
        ))
        self.assertEqual(
            'parent',
            child['native']['dependencies'][0]['referenced_table'],
        )
        view = next(
            item for item in resources
            if item['resource_kind'] == 'view' and
            item['display_name'] == 'shown'
        )
        self.assertIn('CREATE VIEW', view['native']['ddl'])
        self.assertEqual(2, len(view['native']['columns']))

    def test_sqlite_properties_are_native_and_do_not_invent_security(self):
        connection = sqlite3.connect(':memory:')
        try:
            connection.executescript(
                'CREATE TABLE parent(id INTEGER PRIMARY KEY);'
                'CREATE TABLE child('
                ' id INTEGER, parent_id INTEGER REFERENCES parent(id)'
                ');'
                'CREATE INDEX child_parent ON child(parent_id);'
                'CREATE TRIGGER child_ai AFTER INSERT ON child '
                'BEGIN UPDATE child SET id=id WHERE rowid=NEW.rowid; END;'
                'CREATE VIEW shown AS SELECT * FROM child;'
            )
            resources = sqlite_resources(connection, {})
        finally:
            connection.close()
        child = next(
            item for item in resources
            if item['resource_kind'] == 'table' and
            item['display_name'] == 'child'
        )
        self.assertIn('CREATE TABLE', child['native']['ddl'])
        self.assertEqual(2, len(child['native']['columns']))
        self.assertEqual(1, len(child['native']['indexes']))
        self.assertEqual(1, len(child['native']['triggers']))
        self.assertEqual(
            'parent',
            child['native']['dependencies'][0]['referenced_table'],
        )
        self.assertNotIn('privileges', child['native'])
        self.assertNotIn('security', child['native'])
        view = next(
            item for item in resources
            if item['resource_kind'] == 'view' and
            item['display_name'] == 'shown'
        )
        self.assertEqual(2, len(view['native']['columns']))

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
                main = next(
                    item for item in resources
                    if item['resource_kind'] == 'database'
                )
                self.assertEqual('archive', attached['display_name'])
                self.assertEqual(
                    str(root / 'main.sqlite'),
                    main['native']['database_name'],
                )
                self.assertEqual('main', main['native']['schema_name'])
                self.assertEqual(
                    main['native']['page_size_bytes'] *
                    main['native']['page_count'],
                    main['native']['logical_size_bytes'],
                )
                self.assertEqual(
                    sqlite3.sqlite_version,
                    main['native']['sqlite_runtime_version'],
                )
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

    def test_sqlite_discovery_exposes_only_contract_admitted_metrics(self):
        connection = sqlite3.connect(':memory:')
        try:
            resources = sqlite_resources(
                connection, {'capability_generation': 'generation-one'}
            )
        finally:
            connection.close()
        metrics = [
            item for item in resources
            if item['resource_kind'] == 'metric'
        ]
        self.assertEqual(6, len(metrics))
        page_count = next(
            item for item in metrics
            if item['display_name'] == 'page_count'
        )
        self.assertEqual('pages', page_count['native']['unit'])
        self.assertEqual('gauge', page_count['native']['kind'])
        self.assertIsInstance(page_count['native']['value'], int)
        self.assertEqual('generation-one', page_count['generation'])

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
        self.assertEqual(
            'auth_example.so', plugin['native']['definition']['library']
        )
        self.assertEqual('generation-one', plugin['generation'])

    def test_mysql_materialized_view_discovery_uses_show_create(self):
        class Cursor:
            source = ''

            def execute(self, source):
                self.source = source

            def fetchall(self):
                if 'information_schema.TABLES' in self.source:
                    return [
                        ('app', 'live_view', 'VIEW'),
                        ('app', 'stored_view', 'VIEW'),
                    ]
                if self.source == 'SHOW CREATE VIEW `app`.`live_view`':
                    return [('live_view', 'CREATE VIEW `live_view` AS '
                             'SELECT 1')]
                if self.source == 'SHOW CREATE VIEW `app`.`stored_view`':
                    return [('stored_view', 'CREATE MATERIALIZED VIEW '
                             '`stored_view` AS SELECT 1')]
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
        kinds = {
            item['display_name']: item['resource_kind']
            for item in resources
            if item['display_name'] in {'live_view', 'stored_view'}
        }
        self.assertEqual({
            'live_view': 'view',
            'stored_view': 'materialized-view',
        }, kinds)

    def test_mysql_catalog_is_scoped_and_preserves_database_native(self):
        class Cursor:
            source = ''

            def execute(self, source):
                self.source = source

            def fetchall(self):
                if self.source.startswith('SELECT VERSION(), @@hostname'):
                    return [(
                        '9.7.0', 'mysql-host', 3306, 'server-uuid',
                        'MySQL Community Server', 'x86_64', 'Linux', 0,
                        'InnoDB', 'REPEATABLE-READ', 'root@%', 'root@host',
                    )]
                if 'information_schema.SCHEMATA' in self.source:
                    return [
                        ('app', 'utf8mb4', 'utf8mb4_0900_ai_ci', 'NO'),
                        ('sibling', 'utf8mb4', 'utf8mb4_0900_ai_ci', 'NO'),
                    ]
                if 'information_schema.TABLES' in self.source:
                    return [
                        ('app', 'widgets', 'BASE TABLE'),
                        ('sibling', 'private_rows', 'BASE TABLE'),
                    ]
                if self.source == 'SHOW PLUGINS':
                    return [(
                        'mysql_native_password', 'ACTIVE',
                        'AUTHENTICATION', None, 'GPL',
                    )]
                return []

            @staticmethod
            def close():
                return None

        class Connection:
            @staticmethod
            def cursor():
                return Cursor()

        resources = mysql_resources(Connection(), {
            'route': {'database': 'app'},
            'capability_generation': 'generation-one',
        })
        databases = [
            item for item in resources
            if item['resource_kind'] == 'database'
        ]
        self.assertEqual(
            ['app'], [item['display_name'] for item in databases]
        )
        expected_database = {
            'default_character_set': 'utf8mb4',
            'default_collation': 'utf8mb4_0900_ai_ci',
            'default_encryption': 'NO',
        }
        for key, value in expected_database.items():
            self.assertEqual(value, databases[0]['native'][key])
        self.assertEqual(
            expected_database, databases[0]['native']['definition']
        )
        self.assertEqual(
            ['widgets'],
            [item['display_name'] for item in resources
             if item['resource_kind'] == 'table'],
        )
        server = next(
            item for item in resources if item['resource_kind'] == 'server'
        )
        self.assertEqual('server-uuid', server['native']['server_uuid'])
        self.assertTrue(any(
            item['resource_kind'] == 'plugin' for item in resources
        ))

    def test_mariadb_discovery_exposes_only_contract_admitted_metrics(self):
        class Cursor:
            source = ''

            def execute(self, source):
                self.source = source

            def fetchall(self):
                if 'information_schema.TABLES' in self.source:
                    return [('app', 'widgets', 'BASE TABLE')]
                if 'information_schema.GLOBAL_STATUS' in self.source:
                    return [
                        ('THREADS_CONNECTED', '4'),
                        ('CONNECTIONS', '19'),
                    ]
                return []

            @staticmethod
            def close():
                return None

        class Connection:
            @staticmethod
            def cursor():
                return Cursor()

        resources = mysql_resources(
            Connection(), {'capability_generation': 'generation-one'},
            MARIADB_PROFILE,
        )
        metrics = [
            item for item in resources
            if item['resource_kind'] == 'metric'
        ]
        self.assertEqual(28, len(metrics))
        connected = next(
            item for item in metrics
            if item['display_name'] == 'THREADS_CONNECTED'
        )
        self.assertEqual('4', connected['native']['value'])
        self.assertEqual('gauge', connected['native']['kind'])
        absent = next(
            item for item in metrics
            if item['display_name'] == 'BYTES_SENT'
        )
        self.assertEqual(
            'metric_absent_from_exact_runtime',
            absent['native']['observation_error'],
        )

    def test_mariadb_properties_preserve_exact_native_observations(self):
        class Cursor:
            source = ''

            def execute(self, source):
                self.source = source

            def fetchall(self):
                if self.source.startswith('SELECT VERSION(), @@hostname'):
                    return [(
                        '12.2.2-MariaDB', 'mariadb-host', 3306, 41,
                        'mariadb.org binary distribution', 'x86_64',
                        'debian-linux-gnu', 0, 'InnoDB', 'REPEATABLE-READ',
                        'root@localhost', 'root@localhost',
                        'STRICT_TRANS_TABLES', 'utf8mb4',
                        'utf8mb4_uca1400_ai_ci', 1, 'MIXED', 0, 0, 151,
                    )]
                if 'information_schema.SCHEMATA' in self.source:
                    return [(
                        'app', 'utf8mb4', 'utf8mb4_general_ci',
                        'MariaDB application database',
                    )]
                if 'information_schema.TABLES' in self.source:
                    return [('app', 'widgets', 'BASE TABLE')]
                return []

            @staticmethod
            def close():
                return None

        class Connection:
            @staticmethod
            def cursor():
                return Cursor()

        resources = mysql_resources(Connection(), {
            'route': {'database': 'app'},
            'capability_generation': 'generation-one',
        }, MARIADB_PROFILE)
        database = next(
            item for item in resources
            if item['resource_kind'] == 'database'
        )
        expected_database = {
            'default_character_set': 'utf8mb4',
            'default_collation': 'utf8mb4_general_ci',
            'schema_comment': 'MariaDB application database',
        }
        for key, value in expected_database.items():
            self.assertEqual(value, database['native'][key])
        self.assertEqual(expected_database, database['native']['definition'])
        server = next(
            item for item in resources if item['resource_kind'] == 'server'
        )
        self.assertEqual(41, server['native']['server_id'])
        self.assertEqual('REPEATABLE-READ', server['native']['tx_isolation'])
        self.assertEqual('MIXED', server['native']['binlog_format'])
        self.assertEqual(1, server['native']['log_bin'])
        self.assertEqual(0, server['native']['wsrep_on'])
        self.assertEqual(151, server['native']['max_connections'])

    def test_mariadb_binary_log_events_use_exact_bounded_show_command(self):
        class Cursor:
            source = ''
            description = ()
            executed = []

            def execute(self, source):
                self.source = source
                self.executed.append(source)
                if source == 'SHOW BINARY LOGS':
                    self.description = (('Log_name',), ('File_size',))
                elif source.startswith('SHOW BINLOG EVENTS IN '):
                    self.description = (
                        ('Log_name',), ('Pos',), ('Event_type',),
                        ('Server_id',), ('End_log_pos',), ('Info',),
                    )
                else:
                    self.description = ()

            def fetchall(self):
                if self.source == 'SHOW BINARY LOGS':
                    return [('mariadb-bin.000001', 1024)]
                if self.source.startswith('SHOW BINLOG EVENTS IN '):
                    return [(
                        'mariadb-bin.000001', 4, 'Format_desc', 1, 256,
                        'Server ver: 12.2.2-MariaDB',
                    )]
                return []

            @staticmethod
            def close():
                return None

        cursor = Cursor()

        class Connection:
            @staticmethod
            def cursor():
                return cursor

        resources = mysql_resources(
            Connection(), {'capability_generation': 'generation-one'},
            MARIADB_PROFILE,
        )
        event = next(
            item for item in resources
            if item['resource_kind'] == 'binary-log-event'
        )
        self.assertEqual(
            ['mariadb-bin.000001', '4 / Format_desc'],
            event['display_path'],
        )
        self.assertEqual(256, event['native']['end_log_pos'])
        self.assertIn(
            "SHOW BINLOG EVENTS IN 'mariadb-bin.000001' LIMIT 200",
            cursor.executed,
        )

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
            self.assertIn('ResultRenderer', manifest['contracts'])
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

    def test_mysql_family_manifests_admit_native_admin_permissions(self):
        for name in ('mysql_provider_manifest.json',
                     'mariadb_provider_manifest.json'):
            manifest = json.loads((
                PROVIDER_ROOT / 'mysql_family' / name
            ).read_text(encoding='utf-8'))
            grants = {
                item['permission_id']: set(item['scope'])
                for item in manifest['permissions'] if item['granted']
            }
            self.assertEqual(
                {'endpoint', 'resource'}, grants['maintenance_admin']
            )
            self.assertEqual(
                {'endpoint', 'resource'}, grants['backup_admin']
            )
            self.assertEqual(
                {'endpoint', 'resource'}, grants['restore_admin']
            )
            if name == 'mariadb_provider_manifest.json':
                self.assertEqual(
                    {'endpoint', 'resource'}, grants['replication_admin']
                )
                self.assertEqual(
                    {'endpoint', 'resource'}, grants['upgrade_admin']
                )

    def test_mariadb_upgrade_check_honours_native_exit_convention(self):
        calls = []

        class ToolRunner:
            return_code = 1

            def run(self, grant, arguments, **kwargs):
                calls.append((grant.executable_id, list(arguments), kwargs))
                if arguments == ['--version']:
                    return {
                        'return_code': 1,
                        'stdout': 'mariadb-upgrade Distrib 12.2.2-MariaDB',
                        'stderr': '',
                    }
                return {
                    'executable_id': grant.executable_id,
                    'executable_sha256': 'c' * 64,
                    'workspace': grant.workspace,
                    'network_grant': {
                        'host': grant.endpoint_host,
                        'port': grant.endpoint_port,
                    },
                    'return_code': self.return_code,
                    'stdout': '', 'stderr': '',
                    'stdout_truncated': False, 'stderr_truncated': False,
                    'remote_finality_inferred': False,
                    'local_process_observation_only': True,
                }

        runner = ToolRunner()
        config = RelationalClientConfig(
            profile=MARIADB_PROFILE, module_name='test.mariadb.upgrade',
            version_query='SELECT VERSION()',
            connect_arguments=lambda route: route,
            metadata_reader=lambda *_args: [],
        )
        with tempfile.TemporaryDirectory() as workspace:
            client = MariaDBDBAPIClient(
                config, 'mariadb-upgrade-test',
                module=SimpleNamespace(connect=lambda **_kwargs: None),
                tool_runner=runner,
            )
            route = {
                'host': '127.0.0.1', 'port': 3306, 'user': 'operator',
                'tool_workspace': workspace,
            }
            current = client.run_database_operation(
                None, route, 'check_upgrade_required', {}
            )
            self.assertFalse(current['upgrade_required'])
            self.assertTrue(current['check_is_read_only'])
            runner.return_code = 0
            required = client.run_database_operation(
                None, route, 'check_upgrade_required', {}
            )
            self.assertTrue(required['upgrade_required'])
        operation_calls = [item for item in calls if item[1] != ['--version']]
        self.assertEqual(2, len(operation_calls))
        arguments = operation_calls[0][1]
        self.assertEqual('--check-if-upgrade-is-needed', arguments[0])
        self.assertIn('--protocol=tcp', arguments)
        self.assertIn('--host=127.0.0.1', arguments)
        self.assertIn('--port=3306', arguments)
        self.assertFalse(any('password' in item for item in arguments))
        self.assertEqual(
            '--defaults-file={path}',
            operation_calls[0][2]['secret_argument'],
        )

    def test_mysql_shell_backup_uses_workspace_and_stdin_secret(self):
        observations = {}
        leases = []

        class ToolRunner:
            def run(self, grant, arguments, **kwargs):
                observations['grant'] = grant
                observations['arguments'] = arguments
                observations['input'] = kwargs['input_bytes']
                observations['redact_values'] = kwargs['redact_values']
                source = kwargs['secret_config'].decode('utf-8')
                observations['source'] = source
                observations['secret_argument'] = kwargs['secret_argument']
                encoded_path = source.split(
                    'util.dumpSchemas(', 1
                )[1].split(',', 1)[1].lstrip()
                path_value, _end = json.JSONDecoder().raw_decode(encoded_path)
                path = Path(path_value)
                path.mkdir(parents=True)
                (path / '@.json').write_text('{}', encoding='utf-8')
                (path / '@.done.json').write_text('{}', encoding='utf-8')
                (path / 'data.tsv.zst').write_bytes(b'qualified-data')
                return {
                    'executable_id': 'mysql-shell',
                    'executable_sha256': 'a' * 64,
                    'workspace': grant.workspace,
                    'network_grant': {
                        'host': grant.endpoint_host,
                        'port': grant.endpoint_port,
                    },
                    'return_code': 0, 'stdout': 'completed', 'stderr': '',
                    'stdout_truncated': False, 'stderr_truncated': False,
                    'remote_finality_inferred': False,
                    'local_process_observation_only': True,
                }

        def acquire(reference, principal, purpose, expected_kind):
            observations.setdefault('acquisitions', []).append((
                reference, principal, purpose, expected_kind,
            ))
            lease = SecretLease(b'mysql-shell-secret-canary')
            leases.append(lease)
            return lease

        config = RelationalClientConfig(
            profile=MYSQL_PROFILE, module_name='test.mysql.shell',
            version_query='SELECT VERSION()',
            connect_arguments=lambda route: route,
            metadata_reader=lambda *_args: [],
            credential_arguments={'database_password': 'password'},
            secret_acquirer=acquire,
        )
        module = SimpleNamespace(connect=lambda **_kwargs: None)
        with tempfile.TemporaryDirectory() as temporary:
            client = MySQLDBAPIClient(
                config, module=module, tool_runner=ToolRunner()
            )
            result = client.run_database_operation(None, {
                'host': '127.0.0.1', 'port': 3306, 'user': 'operator',
                'database': 'inventory', 'ssl_disabled': True,
                'tool_workspace': temporary,
                'credential_reference_id': 'secret-reference',
                'principal_reference': 'operator-principal',
            }, 'backup_logical', {
                'path': 'backup-one', 'threads': 7,
                'consistent': True, 'compression': 'zstd',
            })
            self.assertEqual(3, result['artifact_file_count'])
            self.assertGreater(result['artifact_total_bytes'], 0)
            self.assertEqual('', result['stdout'])
            self.assertTrue((Path(temporary) / 'backup-one').is_dir())
        self.assertEqual(b'mysql-shell-secret-canary\n', observations['input'])
        self.assertNotIn(
            'mysql-shell-secret-canary', ' '.join(observations['arguments'])
        )
        self.assertEqual(
            ('secret-reference', 'operator-principal', 'provider_tool',
             'database_password'),
            observations['acquisitions'][0],
        )
        self.assertTrue(leases[0].closed)
        self.assertEqual({0}, set(leases[0]._buffer))
        source = observations['source']
        self.assertIn('util.dumpSchemas(["inventory"]', source)
        self.assertIn('"threads":7', source)
        self.assertNotIn('util.dumpSchemas', ' '.join(
            observations['arguments']
        ))
        self.assertEqual('--file={path}', observations['secret_argument'])
        self.assertIn('--ssl-mode=DISABLED', observations['arguments'])

    def test_mysql_shell_paths_and_restore_completion_fail_closed(self):
        config = RelationalClientConfig(
            profile=MYSQL_PROFILE, module_name='test.mysql.shell',
            version_query='SELECT VERSION()',
            connect_arguments=lambda route: route,
            metadata_reader=lambda *_args: [],
        )
        client = MySQLDBAPIClient(
            config, module=SimpleNamespace(connect=lambda **_kwargs: None),
            tool_runner=SimpleNamespace(run=mock.Mock()),
        )
        with tempfile.TemporaryDirectory() as temporary:
            route = {
                'host': '127.0.0.1', 'port': 3306, 'user': 'operator',
                'database': 'inventory', 'tool_workspace': temporary,
            }
            with self.assertRaisesRegex(
                    RelationalClientError, 'escapes'):
                client.run_database_operation(
                    None, route, 'backup_logical', {'path': '../outside'}
                )
            incomplete = Path(temporary) / 'incomplete'
            incomplete.mkdir()
            (incomplete / '@.json').write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(
                    RelationalClientError, 'completed dump'):
                client.run_database_operation(
                    None, route, 'restore_logical', {'path': 'incomplete'}
                )

    def test_mariadb_native_tools_backup_restore_and_verify_artifact(self):
        calls = []

        class Cursor:
            def __init__(self):
                self.rows = []

            def execute(self, source, parameters=None):
                if source == 'SELECT VERSION()':
                    self.rows = [('12.2.2-MariaDB-ubu2404',)]
                elif 'information_schema.TABLES' in source:
                    self.rows = [
                        ('assets', 'BASE TABLE'),
                        ('open_assets', 'VIEW'),
                    ]
                    self.parameters = parameters
                else:
                    raise AssertionError(source)

            def fetchone(self):
                return self.rows[0]

            def fetchall(self):
                return list(self.rows)

            def close(self):
                pass

        class Connection:
            @staticmethod
            def cursor():
                return Cursor()

        class ToolRunner:
            def run(self, grant, arguments, **kwargs):
                calls.append((grant.executable_id, list(arguments), kwargs))
                if arguments == ['--version']:
                    return {
                        'return_code': 0,
                        'stdout': 'client from 12.2.2-MariaDB', 'stderr': '',
                    }
                secret = kwargs['secret_config'].decode('utf-8')
                self.assert_secret(secret)
                self.assert_leading_config(kwargs)
                if grant.executable_id == 'mariadb-dump':
                    output = next(
                        item.split('=', 1)[1] for item in arguments
                        if item.startswith('--result-file=')
                    )
                    Path(output).write_bytes(
                        b'-- MariaDB dump\nCREATE DATABASE inventory;\n'
                    )
                else:
                    self.assert_restore_input(kwargs)
                return {
                    'executable_id': grant.executable_id,
                    'executable_sha256': 'b' * 64,
                    'workspace': grant.workspace,
                    'network_grant': {
                        'host': grant.endpoint_host,
                        'port': grant.endpoint_port,
                    },
                    'return_code': 0, 'stdout': '', 'stderr': '',
                    'stdout_truncated': False, 'stderr_truncated': False,
                    'remote_finality_inferred': False,
                    'local_process_observation_only': True,
                }

            @staticmethod
            def assert_secret(value):
                assert value == '[client]\npassword="mariadb-secret"\n'

            @staticmethod
            def assert_leading_config(kwargs):
                assert kwargs['secret_argument'] == '--defaults-file={path}'
                assert kwargs['secret_argument_position'] == 0

            @staticmethod
            def assert_restore_input(kwargs):
                assert Path(kwargs['input_path']).name == 'inventory.sql'

        leases = []

        def acquire(_reference, _principal, _purpose, _kind):
            lease = SecretLease(b'mariadb-secret')
            leases.append(lease)
            return lease

        config = RelationalClientConfig(
            profile=MARIADB_PROFILE, module_name='test.mariadb.tools',
            version_query='SELECT VERSION()',
            connect_arguments=lambda route: route,
            metadata_reader=lambda *_args: [],
            credential_arguments={'database_password': 'password'},
            secret_acquirer=acquire,
        )
        module = SimpleNamespace(connect=lambda **_kwargs: Connection())
        with tempfile.TemporaryDirectory() as workspace:
            connection = Connection()
            client = MariaDBDBAPIClient(
                config, 'mariadb-tool-test', module=module,
                tool_runner=ToolRunner(),
            )
            client._connect = lambda _request: connection
            client._forget_and_close = lambda _connection: None
            route = {
                'host': '127.0.0.1', 'port': 3306, 'user': 'operator',
                'database': 'inventory', 'tool_workspace': workspace,
                'credential_reference_id': 'secret-reference',
                'principal_reference': 'operator-principal',
            }
            backup = client.run_database_operation(
                connection, route, 'backup_logical', {
                    'path': 'inventory.sql', 'single_transaction': True,
                    'add_drop_database': True, 'routines': True,
                    'events': True, 'triggers': True,
                }
            )
            dump_path = Path(workspace) / 'inventory.sql'
            metadata_path = Path(workspace) / 'inventory.sql.cdeadmin.json'
            self.assertTrue(dump_path.is_file())
            self.assertTrue(metadata_path.is_file())
            self.assertTrue(backup['mariadb_dump_completed'])
            dump_call = next(item for item in calls if item[0] == (
                'mariadb-dump') and item[1] != ['--version'])
            self.assertNotIn('mariadb-secret', ' '.join(dump_call[1]))
            self.assertEqual(
                ['--databases', 'inventory'], dump_call[1][-2:]
            )
            restore = client.run_database_operation(
                connection, route, 'restore_logical', {
                    'path': 'inventory.sql', 'confirmation': 'inventory',
                    'abort_on_error': True, 'binary_mode': True,
                }
            )
            self.assertTrue(restore['mariadb_restore_completed'])
            self.assertTrue(restore['restore_postcondition'][
                'object_postcondition_passed'
            ])
            dump_path.write_bytes(b'tampered')
            with self.assertRaisesRegex(
                    RelationalClientError, 'checksum'):
                client.run_database_operation(
                    connection, route, 'restore_logical', {
                        'path': 'inventory.sql',
                        'confirmation': 'inventory',
                    }
                )
        self.assertEqual(2, len(leases))
        self.assertTrue(all(lease.closed for lease in leases))
        self.assertTrue(all(set(lease._buffer) == {0} for lease in leases))

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
        with mock.patch(
                'pgadmin.cdeadmin.providers.firebird.provider.'
                '_client_library_identity'):
            initialize_firebird_connection(connection, {
                'transaction_isolation': 'READ_COMMITTED_READ_CONSISTENCY',
                'transaction_access': 'READ',
                'transaction_lock_timeout': 15,
            }, firebird_module)
        self.assertIsInstance(connection.default_tpb, bytes)
        self.assertEqual(
            connection.default_tpb, transaction.default_tpb
        )

    def test_firebird_client_library_gate_accepts_generation_five(self):
        class VersionFunction:
            def __call__(self, buffer):
                buffer.value = b'LI-V6.3.4.1812 Firebird 5.0'

        module = SimpleNamespace(get_api=lambda: SimpleNamespace(
            client_library=SimpleNamespace(
                isc_get_client_version=VersionFunction()
            ),
            client_library_name='/runtime/firebird/libfbclient.so.5.0.4',
        ))
        self.assertEqual({
            'client_library_version': '5.0',
            'client_library_name': 'libfbclient.so.5.0.4',
        }, firebird_client_library_identity(module))

    def test_firebird_client_library_gate_rejects_generation_three(self):
        class VersionFunction:
            def __call__(self, buffer):
                buffer.value = b'LI-V6.3.11.33703 Firebird 3.0'

        module = SimpleNamespace(get_api=lambda: SimpleNamespace(
            client_library=SimpleNamespace(
                isc_get_client_version=VersionFunction()
            ),
            client_library_name='/usr/lib/libfbclient.so.2',
        ))
        with self.assertRaisesRegex(
                RelationalClientError, 'Firebird 5 client library'):
            firebird_client_library_identity(module)

    def test_firebird_database_tasks_dispatch_to_exact_service_methods(self):
        import firebird.driver as firebird_module

        class Services:
            def __init__(self):
                self.calls = []

            def _call(self, name, values):
                callback = values.pop('callback', None)
                self.calls.append((name, values))
                if callback:
                    callback(f'{name} complete')

            def backup(self, **values):
                self._call('backup', values)

            def restore(self, **values):
                self._call('restore', values)

            def nbackup(self, **values):
                self._call('nbackup', values)

            def nrestore(self, **values):
                self._call('nrestore', values)

            def validate(self, **values):
                self._call('validate', values)

            def repair(self, **values):
                self._call('repair', values)

            def sweep(self, **values):
                self._call('sweep', values)

            def get_statistics(self, **values):
                self._call('get_statistics', values)

            def shutdown(self, **values):
                self._call('shutdown', values)

            def bring_online(self, **values):
                self._call('bring_online', values)

            def set_default_cache_size(self, **values):
                self._call('set_default_cache_size', values)

            def set_sweep_interval(self, **values):
                self._call('set_sweep_interval', values)

            def set_space_reservation(self, **values):
                self._call('set_space_reservation', values)

            def set_write_mode(self, **values):
                self._call('set_write_mode', values)

            def set_access_mode(self, **values):
                self._call('set_access_mode', values)

            def set_sql_dialect(self, **values):
                self._call('set_sql_dialect', values)

            def activate_shadow(self, **values):
                self._call('activate_shadow', values)

            def no_linger(self, **values):
                self._call('no_linger', values)

            def nfix_database(self, **values):
                self._call('nfix_database', values)

            def set_replica_mode(self, **values):
                self._call('set_replica_mode', values)

            def upgrade(self, **values):
                self._call('upgrade', values)

        services = Services()
        server = SimpleNamespace(database=services)
        cases = (
            ('backup_logical', {
                'backup_file': '/backup/demo.fbk',
                'backup_flags': ['ZIP'], 'verbose': True,
            }, 'backup'),
            ('restore_logical', {
                'backup_file': '/backup/demo.fbk',
                'restore_database': '/data/restored.fdb',
                'restore_flags': [], 'replace_existing': False,
            }, 'restore'),
            ('backup_physical', {
                'backup_file': '/backup/demo.nbk', 'backup_level': 0,
                'backup_flags': [],
            }, 'nbackup'),
            ('restore_physical', {
                'backup_files': ['/backup/demo.nbk'],
                'restore_database': '/data/restored.fdb',
                'restore_flags': [],
            }, 'nrestore'),
            ('validate_database', {'lock_timeout': 10}, 'validate'),
            ('repair_database', {'repair_action': 'VALIDATE_DB'}, 'repair'),
            ('sweep_database', {}, 'sweep'),
            ('database_statistics', {
                'statistics_flags': ['HDR_PAGES'], 'tables': [],
            }, 'get_statistics'),
            ('shutdown_database', {
                'mode': 'FULL', 'method': 'DENY_ATTACHMENTS',
                'shutdown_timeout': 0,
            }, 'shutdown'),
            ('bring_online', {'mode': 'NORMAL'}, 'bring_online'),
            ('set_page_cache_size', {'page_buffers': 4096},
             'set_default_cache_size'),
            ('set_sweep_interval', {'sweep_interval': 20000},
             'set_sweep_interval'),
            ('set_space_reservation', {'mode': 'RESERVE'},
             'set_space_reservation'),
            ('set_write_mode', {'mode': 'SYNC'}, 'set_write_mode'),
            ('set_access_mode', {'mode': 'READ_WRITE'}, 'set_access_mode'),
            ('set_sql_dialect', {'sql_dialect': '3'}, 'set_sql_dialect'),
            ('activate_shadow', {}, 'activate_shadow'),
            ('remove_linger', {}, 'no_linger'),
            ('fixup_database', {'fixup_flags': ['SEQUENCE']},
             'nfix_database'),
            ('set_replica_mode', {'mode': 'NONE'}, 'set_replica_mode'),
            ('upgrade_database', {}, 'upgrade'),
        )
        for operation, options, expected_method in cases:
            result = _firebird_service_operation(
                server, operation, '/data/demo.fdb', options,
                firebird_module,
            )
            self.assertTrue(result['server_completed'])
            self.assertEqual(expected_method, services.calls[-1][0])
        self.assertEqual(
            firebird_module.SrvBackupFlag.ZIP,
            services.calls[0][1]['flags'],
        )
        self.assertEqual(
            firebird_module.SrvRestoreFlag.CREATE,
            services.calls[1][1]['flags'],
        )
        self.assertEqual(
            ['backup complete'],
            _firebird_service_operation(
                server, 'backup_logical', '/data/demo.fdb', {
                    'backup_file': '/backup/second.fbk',
                    'backup_flags': [], 'verbose': True,
                }, firebird_module,
            )['output'],
        )

        invalid_physical_flags = (
            ('backup_physical', {
                'backup_file': '/backup/invalid.nbk',
                'backup_flags': ['IN_PLACE'],
            }),
            ('restore_physical', {
                'backup_files': ['/backup/demo.nbk'],
                'restore_database': '/data/invalid.fdb',
                'restore_flags': ['NO_TRIGGERS'],
            }),
            ('fixup_database', {'fixup_flags': ['NO_TRIGGERS']}),
        )
        for operation, options in invalid_physical_flags:
            with self.subTest(operation=operation), self.assertRaises(
                    RelationalClientError):
                _firebird_service_operation(
                    server, operation, '/data/demo.fdb', options,
                    firebird_module,
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

    def test_mariadb_direct_connection_strips_pool_only_defaults(self):
        observed = {}
        handle = object()
        module = SimpleNamespace(
            connect=lambda *args, **kwargs: (
                observed.update({'args': args, 'options': kwargs}) or handle
            ),
            ConnectionPool=mock.Mock(),
        )
        facade = _MariaDBConnectorFacade(module, 'direct-namespace')

        result = facade(
            host='db.example', user='operator',
            pool_reset_connection=True,
            pool_validation_interval=500,
        )

        self.assertIs(handle, result)
        self.assertEqual({
            'host': 'db.example', 'user': 'operator',
        }, observed['options'])
        module.ConnectionPool.assert_not_called()

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

    def test_mariadb_results_are_provider_streamed_in_bounded_pages(self):
        observations = {'fetch_sizes': [], 'fetchall': 0}

        class Cursor:
            description = (('value', 'INTEGER'),)
            rowcount = -1

            def __init__(self):
                self.batches = [
                    [(value,) for value in range(500)],
                    [(500,)],
                    [],
                ]

            @staticmethod
            def execute(source, parameters=None):
                assert source == 'SELECT value FROM large_result'
                assert parameters is None

            def fetchmany(self, size):
                observations['fetch_sizes'].append(size)
                return self.batches.pop(0)

            @staticmethod
            def fetchall():
                observations['fetchall'] += 1
                raise AssertionError('streamed execution used fetchall')

            @staticmethod
            def close():
                return None

        class Connection:
            thread_id = 41

            @staticmethod
            def cursor(**options):
                assert options == {'buffered': False}
                return Cursor()

            @staticmethod
            def close():
                return None

        module = SimpleNamespace(
            connect=lambda **_kwargs: Connection(), ConnectionPool=mock.Mock()
        )
        client = MariaDBDBAPIClient(RelationalClientConfig(
            profile=MARIADB_PROFILE,
            module_name='test.mariadb.streaming',
            version_query='SELECT VERSION()',
            connect_arguments=lambda route: dict(route),
            metadata_reader=lambda *_args: [],
        ), 'streaming-namespace', module)
        handle = client.open_session({'route': {'host': 'db.example'}})
        try:
            token = client.execute(handle, {
                'source': 'SELECT value FROM large_result',
                'parameters': (),
            })
            first = client.describe_result(token)
            second = client.describe_result(token)
            self.assertFalse(first['complete'])
            self.assertEqual(500, len(first['payload']['rows']))
            self.assertTrue(second['complete'])
            self.assertEqual([(500,)], second['payload']['rows'])
            self.assertTrue(first['payload']['streamed_by_provider'])
            self.assertEqual(
                first['stream_reference'], second['stream_reference']
            )
            self.assertEqual([500, 500, 500], observations['fetch_sizes'])
            self.assertEqual(0, observations['fetchall'])
        finally:
            client.close()

    def test_mariadb_active_query_uses_native_kill_query_control_session(self):
        started = threading.Event()
        interrupted = threading.Event()
        statements = []

        class QueryInterrupted(Exception):
            errno = 1317
            sqlstate = '70100'

        class QueryCursor:
            description = ()
            rowcount = -1

            @staticmethod
            def execute(source, parameters=None):
                assert source == 'SELECT SLEEP(30)'
                assert parameters is None
                started.set()
                if not interrupted.wait(2):
                    raise AssertionError('native cancellation was not issued')
                raise QueryInterrupted()

            @staticmethod
            def close():
                return None

        class ControlCursor:
            @staticmethod
            def execute(source):
                statements.append(source)
                interrupted.set()

            @staticmethod
            def close():
                return None

        class QueryConnection:
            thread_id = 73

            @staticmethod
            def cursor(**options):
                assert options == {'buffered': False}
                return QueryCursor()

            @staticmethod
            def close():
                return None

        class ControlConnection:
            @staticmethod
            def cursor():
                return ControlCursor()

            @staticmethod
            def close():
                return None

        connections = iter((QueryConnection(), ControlConnection()))
        module = SimpleNamespace(
            connect=lambda **_kwargs: next(connections),
            ConnectionPool=mock.Mock(),
        )
        client = MariaDBDBAPIClient(RelationalClientConfig(
            profile=MARIADB_PROFILE,
            module_name='test.mariadb.cancellation',
            version_query='SELECT VERSION()',
            connect_arguments=lambda route: dict(route),
            metadata_reader=lambda *_args: [],
        ), 'cancellation-namespace', module)
        handle = client.open_session({'route': {'host': 'db.example'}})
        try:
            token = client.execute(handle, {
                'source': 'SELECT SLEEP(30)', 'parameters': (),
            })
            self.assertTrue(started.wait(1))
            self.assertTrue(client.cancel(token))
            token.worker.join(timeout=2)
            result = client.describe_result(token)
            self.assertTrue(result['complete'])
            self.assertTrue(result['payload']['cancelled'])
            self.assertEqual(['KILL QUERY 73'], statements)
        finally:
            client.close()


class RelationalDBAPIClientTests(unittest.TestCase):

    def test_server_scope_uses_distinct_connector_and_blocks_sessions(self):
        calls = []

        class Handle:
            def __init__(self, kind):
                self.kind = kind

            def close(self):
                calls.append(('close', self.kind))

        module = SimpleNamespace(
            connect=lambda **kwargs: calls.append(('database', kwargs)),
            connect_server=lambda **_kwargs: Handle('server'),
        )
        profile = installed_sqlite_profile()
        client = RelationalDBAPIClient(RelationalClientConfig(
            profile=profile, module_name='server-scope-test',
            version_query='SELECT sqlite_version()',
            connect_arguments=lambda route: {'database': route['database']},
            metadata_reader=lambda _connection, _request: [],
            server_route=lambda route: not route.get('database'),
            server_connector_name='connect_server',
            server_connect_arguments=lambda route: {'server': route['host']},
            server_identity_reader=lambda _server, _request: {
                'engine_id': profile.engine_id,
                'version': profile.exact_version,
                'build_id': 'server-scope-test',
                'protocol_id': profile.protocol_id,
            },
            server_metadata_reader=lambda _server, _request: [{
                'resource_id': 'server:test', 'resource_kind': 'server',
                'display_name': 'test', 'authority_path': ['server', 'test'],
                'generation': 'one',
            }],
        ), module)
        request = {'route': {'host': 'server.example'}}
        self.assertEqual(
            'server-scope-test', client.runtime_identity(request)['build_id']
        )
        self.assertEqual(
            'server:test', client.list_resources(request)[0]['resource_id']
        )
        self.assertEqual([('close', 'server'), ('close', 'server')], calls)
        with self.assertRaisesRegex(
            RelationalClientError, 'select or create a database'
        ):
            client.open_session(request)

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
        controlled = self.provider.control_transaction({
            'session_id': session['session_id'], 'action': 'rollback',
        })
        self.assertTrue(controlled['provider_payload'][
            'driver_observation_only'
        ])

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

    def test_secret_acquisition_failure_has_credential_classification(self):
        def acquire(*_args):
            raise RuntimeError('protected-secret-canary')

        client = RelationalDBAPIClient(RelationalClientConfig(
            profile=self.profile,
            module_name='test.secret.unavailable.dbapi',
            version_query='SELECT sqlite_version()',
            connect_arguments=lambda route: dict(route),
            metadata_reader=lambda *_args: [],
            credential_argument='password',
            secret_acquirer=acquire,
        ), SimpleNamespace(connect=mock.Mock()))
        with self.assertRaises(RelationalCredentialError) as raised:
            client.open_session({'route': {
                'credential_reference_id': 'reference-one',
                'principal_reference': 'principal-one',
            }})
        self.assertNotIn('protected-secret-canary', str(raised.exception))

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
