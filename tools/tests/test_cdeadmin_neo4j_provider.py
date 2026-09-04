##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Neo4j graph provider, driver boundary, and administration tests."""

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

from pgadmin.cdeadmin.providers.neo4j.client import (  # noqa: E402
    Neo4jClient,
    Neo4jClientError,
    Neo4jDependencyError,
    QUALIFIED_GDS_SHA256,
    _record_data,
)
from pgadmin.cdeadmin.providers.neo4j.provider import (  # noqa: E402
    Neo4jPilotProvider,
    PROFILE,
    create_provider,
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


class Node(dict):
    def __init__(self, element_id, labels, **properties):
        super().__init__(properties)
        self.element_id = element_id
        self.labels = frozenset(labels)


class Relationship(dict):
    def __init__(self, element_id, rel_type, start, end, **properties):
        super().__init__(properties)
        self.element_id = element_id
        self.type = rel_type
        self.start_node = start
        self.end_node = end


class Record(dict):
    def data(self):
        return dict(self)


class Counters:
    nodes_created = 0
    nodes_deleted = 0
    relationships_created = 0
    relationships_deleted = 0
    properties_set = 0
    labels_added = 0
    labels_removed = 0
    indexes_added = 0
    indexes_removed = 0
    constraints_added = 0
    constraints_removed = 0


class Result:
    def __init__(self, records, keys=None):
        self.records = list(records)
        self.offset = 0
        self._keys = keys or list(self.records[0]) if self.records else []
        self.cancelled = False

    def __iter__(self):
        return iter(self.records)

    def keys(self):
        return self._keys

    def single(self):
        return self.records[0] if self.records else None

    def fetch(self, count):
        records = self.records[self.offset:self.offset + count]
        self.offset += len(records)
        return records

    def consume(self):
        return SimpleNamespace(
            counters=Counters(), query_type='r',
            result_available_after=1, result_consumed_after=2,
        )

    def cancel(self):
        self.cancelled = True


class Session:
    def __init__(self, driver, database, options=None):
        self.driver = driver
        self.database = database
        self.options = options or {}
        self.closed = False

    def close(self):
        self.closed = True

    def run(self, statement, parameters=None):
        parameters = parameters or {}
        self.driver.statements.append((self.database, statement, parameters))
        if 'dbms.components' in statement:
            return Result([Record(
                name='Neo4j Kernel', versions=['2026.04.0'],
                edition='community',
            )])
        if statement.startswith('SHOW DATABASES'):
            return Result([
                Record(
                    name='neo4j', type='standard', currentStatus='online',
                    serverId='server-one', access='read-write',
                ),
                Record(
                    name='neo4j', type='standard', currentStatus='online',
                    serverId='server-two', access='read-write',
                ),
                Record(
                    name='fabric', type='composite', currentStatus='online'
                ),
            ])
        if statement.startswith('CALL db.labels'):
            return Result([Record(label='Person')])
        if statement.startswith('CALL db.relationshipTypes'):
            return Result([Record(relationshipType='KNOWS')])
        if statement.startswith('CALL db.propertyKeys'):
            return Result([Record(propertyKey='name')])
        if statement.startswith('SHOW INDEXES'):
            return Result([Record(name='person_name', type='RANGE')])
        if statement.startswith('SHOW CONSTRAINTS'):
            return Result([Record(name='person_id', type='UNIQUENESS')])
        if statement.startswith('SHOW USERS'):
            return Result([Record(user='neo4j', roles=['admin'])])
        if statement.startswith('SHOW ROLES'):
            return Result([Record(role='admin')])
        if statement.startswith('SHOW PRIVILEGES'):
            return Result([Record(action='ACCESS', role='admin')])
        if statement.startswith('SHOW') or statement.startswith('CALL'):
            return Result([])
        if 'MATCH (n) OPTIONAL MATCH' in statement:
            alice = Node('4:one', ['Person'], name='Alice')
            bob = Node('4:two', ['Person'], name='Bob')
            knows = Relationship('5:one', 'KNOWS', alice, bob, since=2020)
            return Result([Record(n=alice, r=knows, m=bob)])
        if statement.startswith('CREATE (n'):
            node = Node('4:new', ['Person'], **parameters['properties'])
            return Result([Record(n=node)])
        return Result([Record(ok=True)])


class Driver:
    def __init__(self, uri, auth, options):
        self.uri = uri
        self.auth = auth
        self.options = options
        self.statements = []
        self.sessions = []
        self.closed = False

    def session(self, database=None, **options):
        value = Session(self, database, options)
        self.sessions.append(value)
        return value

    def verify_connectivity(self):
        return None

    def get_server_info(self):
        return SimpleNamespace(
            agent='Neo4j/2026.04.0', protocol_version=(5, 8),
        )

    def close(self):
        self.closed = True


class Connector:
    def __init__(self):
        self.drivers = []

    def __call__(self, uri, auth=None, **options):
        value = Driver(uri, auth, options)
        self.drivers.append(value)
        return value


class Permissions:
    def require(self, _permission, _scope='endpoint'):
        return None

    def allows(self, _permission, _scope='endpoint'):
        return True

    @staticmethod
    def acquire_secret(*_args):
        return SecretLease()


def route(**changes):
    value = {
        'host': 'localhost', 'port': 7687, 'database': 'neo4j',
        'username': 'neo4j', 'credential_reference_id': 'credential-one',
        'principal_reference': 'principal-one', 'routing': True,
        'tls_mode': 'disabled',
    }
    value.update(changes)
    return value


def client(secret_acquirer=None):
    connector = Connector()
    module = SimpleNamespace(
        GraphDatabase=SimpleNamespace(driver=connector),
        basic_auth=lambda user, secret, realm=None:
            ('basic', user, secret, realm),
        kerberos_auth=lambda secret: ('kerberos', secret),
        bearer_auth=lambda secret: ('bearer', secret),
        custom_auth=lambda user, secret, realm, scheme, **parameters:
            ('custom', user, secret, realm, scheme, parameters),
        READ_ACCESS='READ', WRITE_ACCESS='WRITE',
        Bookmarks=SimpleNamespace(
            from_raw_values=lambda values: ('bookmarks', tuple(values))
        ),
        Address=SimpleNamespace(
            parse=lambda value: tuple(value.rsplit(':', 1))
        ),
    )
    acquire = secret_acquirer or (lambda *_args: SecretLease())
    return Neo4jClient(acquire, module), connector


def context():
    return SimpleNamespace(
        endpoint_id='endpoint', mode='legacy_native',
        runtime_verification_state='verified',
        verified_runtime_family='neo4j',
        declared_runtime_family='neo4j',
        effective_permissions=frozenset({
            'network', 'secret_read', 'data_read', 'data_write',
            'administer', 'execute', 'filesystem',
        }),
        session_namespace='session', cache_namespace='cache',
    )


class Neo4jProviderTests(unittest.TestCase):

    def test_profile_is_full_graph_surface_and_production_renderer(self):
        self.assertEqual('graph', PROFILE.model_family)
        self.assertEqual(
            'application/vnd.neo4j.cypher', PROFILE.language_mime_type
        )
        self.assertEqual(
            'cdeadmin.result.graph.canvas', PROFILE.result_renderer_id
        )
        self.assertEqual('graphs', PROFILE.result_records_field)
        self.assertEqual(29, len(PROFILE.resource_kinds))
        renderer = next(
            item for item in builtin_renderers()
            if item.renderer_id == 'cdeadmin.result.graph.canvas'
        )
        self.assertFalse(renderer.fixture_safe)
        self.assertEqual(
            frozenset({'json', 'jsonl'}), renderer.export_formats
        )

    def test_structured_route_secret_lease_and_tls_scheme(self):
        leases = []

        def acquire(*_args):
            value = SecretLease()
            leases.append(value)
            return value

        adapter, connector = client(acquire)
        identity = adapter.runtime_identity({'route': route(
            routing=False, tls_mode='system-ca'
        )}, None)
        self.assertEqual('2026.04.0', identity['version'])
        self.assertEqual('bolt+s://localhost:7687', connector.drivers[0].uri)
        self.assertEqual(
            ('basic', 'neo4j', 'correct-horse', None),
            connector.drivers[0].auth,
        )
        self.assertTrue(leases[0].closed)
        self.assertEqual(bytearray(len(b'correct-horse')), leases[0].value)

    def test_all_bolt_authentication_tokens_are_driver_owned(self):
        scenarios = (
            ('kerberos', 'authentication_token', (
                'kerberos', 'correct-horse',
            )),
            ('bearer', 'authentication_token', ('bearer', 'correct-horse')),
            ('custom', 'custom_auth_credentials', (
                'custom', 'neo4j', 'correct-horse', 'realm-one',
                'scheme-one', {'answer': 42},
            )),
        )
        for mode, kind, expected in scenarios:
            with self.subTest(mode=mode):
                adapter, connector = client()
                values = route(
                    auth_mode=mode,
                    credential_references={kind: 'credential-one'},
                )
                values.pop('credential_reference_id')
                if mode in {'kerberos', 'bearer'}:
                    values.pop('username')
                else:
                    values.update({
                        'auth_realm': 'realm-one',
                        'auth_scheme': 'scheme-one',
                        'auth_parameters': {'answer': 42},
                    })
                handle = adapter.open_session({'route': values})
                self.assertEqual(expected, connector.drivers[0].auth)
                handle.close()

    def test_session_defaults_are_forwarded_to_the_bolt_driver(self):
        adapter, connector = client()
        handle = adapter.open_session({'route': route(
            access_mode='read', fetch_size=250,
            impersonated_user='auditor',
            bookmarks=['bookmark-one', 'bookmark-two'],
        )})
        native = connector.drivers[0].sessions[0]
        self.assertEqual('neo4j', native.database)
        self.assertEqual('READ', native.options['default_access_mode'])
        self.assertEqual(250, native.options['fetch_size'])
        self.assertEqual('auditor', native.options['impersonated_user'])
        self.assertEqual(
            ('bookmarks', ('bookmark-one', 'bookmark-two')),
            native.options['bookmarks'],
        )
        handle.close()

    def test_resolver_addresses_are_parsed_for_driver_failover(self):
        adapter, connector = client()
        adapter.runtime_identity({'route': route(
            resolver_addresses=[
                '127.0.0.1:7687', '127.0.0.2:7687',
            ],
        )})
        resolver = connector.drivers[0].options['resolver']
        self.assertEqual((
            ('127.0.0.1', '7687'), ('127.0.0.2', '7687'),
        ), resolver(('ignored', 7687)))

    def test_route_refuses_uri_and_unknown_controls(self):
        adapter, _connector = client()
        with self.assertRaises(Neo4jClientError):
            adapter.runtime_identity({'route': {
                **route(), 'uri': 'neo4j://hidden:7687'
            }}, None)

    def test_unqualified_driver_version_fails_closed(self):
        connector = Connector()
        module = SimpleNamespace(
            __version__='6.2.0',
            GraphDatabase=SimpleNamespace(driver=connector),
        )
        with self.assertRaisesRegex(Neo4jDependencyError, 'qualified 6.3.0'):
            Neo4jClient(lambda *_args: SecretLease(), module)

    def test_bolt_result_preserves_graph_identity_and_opaque_finality(self):
        adapter, _connector = client()
        handle = adapter.open_session({'route': route()})
        token = adapter.execute(handle, {
            'source': 'MATCH (n) OPTIONAL MATCH (n)-[r]->(m) RETURN n, r, m',
            'parameters': {},
        })
        result = adapter.describe_result(token)
        self.assertTrue(result['complete'])
        record = result['payload']['graphs'][0]
        self.assertEqual('node', record['n']['kind'])
        self.assertEqual('4:one', record['n']['element_id'])
        self.assertEqual('relationship', record['r']['kind'])
        self.assertEqual('4:two', record['r']['end_node_element_id'])
        transaction = adapter.describe_transaction(handle)
        self.assertFalse(transaction['common_finality_inference'])
        self.assertFalse(transaction['retry_decision_owned_by_common_code'])

    def test_record_conversion_does_not_flatten_native_graph_values(self):
        node = Node('4:preserved', ['Person'], name='Ada')

        class FlatteningRecord(dict):
            def data(self):
                return {'n': dict(self['n'])}

        value = Neo4jClient._json_value(_record_data(
            FlatteningRecord(n=node)
        ))
        self.assertEqual('node', value['n']['kind'])
        self.assertEqual('4:preserved', value['n']['element_id'])
        self.assertEqual(['Person'], value['n']['labels'])

    def test_discovery_is_category_isolated_and_includes_graph(self):
        adapter, connector = client()
        resources = adapter.list_resources({'route': route()})
        kinds = {item['resource_kind'] for item in resources}
        self.assertTrue({
            'dbms', 'database', 'composite-database', 'graph', 'label',
            'relationship-type', 'property', 'index', 'constraint',
            'user', 'role', 'privilege', 'node', 'relationship',
            'query-plan',
        }.issubset(kinds))
        statements = [
            statement for driver in connector.drivers
            for _database, statement, _parameters in driver.statements
        ]
        self.assertIn('CALL gds.graph.list()', statements)
        self.assertNotIn(
            'CALL gds.graph.list() YIELD * RETURN *', statements
        )
        databases = [
            item for item in resources
            if item['resource_kind'] == 'database' and
            item['display_name'] == 'neo4j'
        ]
        self.assertEqual(1, len(databases))
        self.assertEqual(2, databases[0]['native']['clusterMemberCount'])
        self.assertEqual(
            ['server-one', 'server-two'],
            [
                item['serverId']
                for item in databases[0]['native']['clusterMembers']
            ],
        )

    def test_graph_page_and_node_create_use_parameters(self):
        adapter, connector = client()
        target = {
            'resource_kind': 'graph',
            'extensions': {'neo4j': {'native': {
                'name': 'neo4j', 'database': 'neo4j',
            }}},
        }
        page = adapter.read_admin_rows({
            '_provider_route': route(), 'target_resource': target, 'limit': 10,
        })
        self.assertEqual('cdeadmin.visual-admin.graph-page.v1', page['schema'])
        self.assertEqual('node', page['records'][0]['n']['kind'])
        plan = adapter.plan_admin_operation({
            'resource_kind': 'node', 'operation_id': 'insert',
            'target_resource': target,
            'draft': {'values': {
                'labels': ['Person'], 'properties': {'name': 'Cypher text'},
            }, 'options': {}},
            '_provider_route': route(),
        })
        result = adapter.apply_admin_operation({
            'provider_payload': plan['provider_payload'],
        })
        statement = connector.drivers[-1].statements[-1][1]
        parameters = connector.drivers[-1].statements[-1][2]
        self.assertIn('CREATE (n:`Person`)', statement)
        self.assertNotIn('Cypher text', statement)
        self.assertEqual('Cypher text', parameters['properties']['name'])
        self.assertFalse(
            result['transaction_finality_interpreted_by_common_code']
        )

    def test_admin_validation_refuses_raw_cypher(self):
        adapter, _connector = client()
        result = adapter.validate_admin_operation({
            'resource_kind': 'index', 'operation_id': 'create',
            'draft': {
                'name': 'person_name',
                'definition': 'CREATE INDEX injected',
                'options': {'label': 'Person', 'properties': ['name']},
            },
        })
        self.assertEqual('raw_cypher_forbidden', result['errors'][0]['code'])

    def test_database_and_security_plans_match_exact_native_grammar(self):
        adapter, _connector = client()
        statement, parameters = adapter._database_command(
            'database', 'create', {
                'name': 'analytics',
                'options': {
                    'default_language': 'CYPHER 25',
                    'topology': {'primaries': 1, 'secondaries': 2},
                    'wait_seconds': 5,
                },
            }, {},
        )
        self.assertIn('CREATE DATABASE `analytics` IF NOT EXISTS', statement)
        self.assertIn('DEFAULT LANGUAGE CYPHER 25', statement)
        self.assertIn('TOPOLOGY $primaries PRIMARY', statement)
        self.assertEqual(
            {'primaries': 1, 'secondaries': 2},
            parameters,
        )
        statement, parameters = adapter._security_command(
            'user', 'alter', {'changes': {'status': 'suspended'}},
            {'user': 'operator'}, route(),
        )
        self.assertEqual(
            'ALTER USER `operator` IF EXISTS SET STATUS SUSPENDED',
            statement,
        )
        self.assertEqual({}, parameters)
        statement, _parameters = adapter._security_command(
            'role', 'revoke', {
                'principal': 'operator', 'privileges': {},
                'confirmation': 'revoke',
            }, {'role': 'reader'}, route(),
        )
        self.assertEqual(
            'REVOKE ROLE `reader` FROM `operator`', statement
        )
        statement, _parameters = adapter._security_command(
            'privilege', 'grant', {
                'principal': 'analyst',
                'privileges': {
                    'action': 'read', 'scope': 'graph', 'graph': 'neo4j',
                    'properties': ['name'],
                    'resource': {'kind': 'nodes', 'names': ['Person']},
                },
            }, {'name': 'read-person'}, route(),
        )
        self.assertEqual(
            'GRANT READ {`name`} ON GRAPH `neo4j` NODES `Person` '
            'TO `analyst`', statement,
        )

    def test_enterprise_database_forms_compile_typed_native_commands(self):
        adapter, _connector = client()
        statement, parameters = adapter._database_command(
            'database', 'create', {
                'name': 'analytics', 'database_kind': 'standard',
                'default_language': 'CYPHER 25',
                'primaries': 3, 'secondaries': 1,
                'store_format': 'block', 'tx_log_enrichment': 'FULL',
                'wait_mode': 'wait', 'wait_seconds': 30,
            }, {},
        )
        self.assertEqual(
            'CREATE DATABASE `analytics` IF NOT EXISTS '
            'DEFAULT LANGUAGE CYPHER 25 TOPOLOGY $primaries PRIMARY '
            '$secondaries SECONDARY OPTIONS $database_options '
            'WAIT 30 SECONDS',
            statement,
        )
        self.assertEqual({
            'primaries': 3, 'secondaries': 1,
            'database_options': {
                'storeFormat': 'block', 'txLogEnrichment': 'FULL',
            },
        }, parameters)
        statement, parameters = adapter._database_command(
            'database', 'create', {
                'name': 'sharded', 'database_kind': 'sharded',
                'default_language': 'CYPHER 25',
                'graph_shard_primaries': 3,
                'property_shard_count': 4,
                'property_shard_replicas': 2,
            }, {},
        )
        self.assertTrue(statement.startswith(
            'CYPHER 25 CREATE DATABASE `sharded`'
        ))
        self.assertEqual(4, parameters['property_shard_count'])
        statement, parameters = adapter._database_command(
            'database', 'alter', {
                'action': 'configure', 'database_kind': 'standard',
                'access': 'read-only', 'tx_log_enrichment': 'DIFF',
                'wait_mode': 'nowait',
            }, {'name': 'analytics'},
        )
        self.assertEqual(
            'ALTER DATABASE `analytics` SET ACCESS READ ONLY '
            'SET OPTION `txLogEnrichment` $tx_log_enrichment NOWAIT',
            statement,
        )
        self.assertEqual({'tx_log_enrichment': 'DIFF'}, parameters)
        statement, _parameters = adapter._database_command(
            'composite-database', 'alter', {
                'action': 'configure', 'default_language': 'CYPHER 25',
            }, {'name': 'federated'},
        )
        self.assertEqual(
            'ALTER DATABASE `federated` SET DEFAULT LANGUAGE CYPHER 25',
            statement,
        )

    def test_cluster_member_forms_compile_typed_native_commands(self):
        adapter, _connector = client()
        native = {'serverId': 'server-1', 'name': 'primary-east'}
        statement, parameters = adapter._operational_command(
            'server', 'alter', {
                'mode_constraint': 'PRIMARY',
                'database_filter': 'allow',
                'database_patterns': ['sales*'], 'tags': ['east'],
            }, native,
        )
        self.assertEqual(
            'ALTER SERVER $server SET OPTIONS $options', statement
        )
        self.assertEqual({
            'modeConstraint': 'PRIMARY',
            'allowedDatabases': ['sales*'], 'tags': ['east'],
        }, parameters['options'])
        statement, parameters = adapter._operational_command(
            'server', 'execute', {
                'action': 'deallocate-dry-run',
            }, native,
        )
        self.assertEqual(
            'DRYRUN DEALLOCATE DATABASES FROM SERVER $server', statement
        )
        self.assertEqual('server-1', parameters['server'])
        with self.assertRaisesRegex(Neo4jClientError, 'confirmation'):
            adapter._operational_command(
                'server', 'execute', {
                    'action': 'drop', 'confirmation': 'wrong',
                }, native,
            )
        with self.assertRaisesRegex(Neo4jClientError, 'duplicates'):
            adapter._operational_command(
                'server', 'alter', {
                    'mode_constraint': 'NONE', 'database_filter': 'any',
                    'tags': ['east', 'east'],
                }, native,
            )

    def test_enterprise_typed_forms_plan_without_generic_escape_fields(self):
        adapter, _connector = client()
        provider = Neo4jPilotProvider(context(), Permissions(), adapter)
        plan = provider.plan_visual_admin({
            'resource_kind': 'database', 'operation_id': 'create',
            'draft': {
                'name': 'analytics', 'database_kind': 'standard',
                'default_language': 'CYPHER 25', 'primaries': 3,
                'secondaries': 0, 'store_format': 'block',
                'tx_log_enrichment': 'OFF', 'wait_mode': 'wait',
                'wait_seconds': 15,
            }, '_provider_route': route(),
        })
        self.assertEqual('ready', plan['state'])
        self.assertNotIn(
            'definition', plan['command_preview']['arguments']
        )
        target = {
            'resource_kind': 'server',
            'extensions': {'neo4j': {'native': {
                'serverId': 'server-1', 'name': 'primary-east',
            }}},
        }
        plan = provider.plan_visual_admin({
            'resource_kind': 'server', 'operation_id': 'execute',
            'target_resource': target,
            'draft': {
                'action': 'deallocate', 'mode_constraint': 'NONE',
                'database_filter': 'any', 'tags': [],
                'confirmation': 'server-1',
            }, '_provider_route': route(),
        })
        self.assertEqual('ready', plan['state'])
        self.assertNotIn('arguments', plan['command_preview']['arguments'])

    def test_nonexistent_native_rename_operations_are_not_advertised(self):
        adapter, _connector = client()
        self.assertFalse(adapter.supports_admin_operation('index', 'rename'))
        self.assertFalse(adapter.supports_admin_operation(
            'constraint', 'rename'
        ))
        self.assertFalse(adapter.supports_admin_operation(
            'database', 'rename'
        ))

    def test_descriptor_exposes_only_native_supported_operations(self):
        adapter, _connector = client()
        provider = Neo4jPilotProvider(context(), Permissions(), adapter)
        descriptor = provider.visual_admin_descriptor()
        by_kind = {
            item['resource_kind']: item for item in descriptor['objects']
        }
        self.assertTrue(next(
            item for item in by_kind['node']['operations']
            if item['operation_id'] == 'insert'
        )['execution_available'])
        self.assertFalse(next(
            item for item in by_kind['label']['operations']
            if item['operation_id'] == 'create'
        )['native_supported'])
        self.assertEqual('neo4j-driver-and-server', descriptor[
            'transaction_authority'
        ])
        coverage = descriptor['concept_coverage']
        self.assertTrue(coverage['declaration_ready'])
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        self.assertEqual(
            ['graph'],
            [item['family_id'] for item in coverage['families']],
        )
        projection_create = next(
            item for item in by_kind['graph-projection']['operations']
            if item['operation_id'] == 'create'
        )
        self.assertFalse(projection_create['native_supported'])
        self.assertIn(
            'provider_operation_unavailable', projection_create['blockers']
        )
        database_create = next(
            item for item in by_kind['database']['operations']
            if item['operation_id'] == 'create'
        )
        database_fields = {
            field['field_id'] for field in database_create['form']['fields']
        }
        self.assertIn('database_kind', database_fields)
        self.assertIn('tx_log_enrichment', database_fields)
        self.assertNotIn('options', database_fields)
        server_alter = next(
            item for item in by_kind['server']['operations']
            if item['operation_id'] == 'alter'
        )
        server_fields = {
            field['field_id'] for field in server_alter['form']['fields']
        }
        self.assertEqual({
            'mode_constraint', 'database_filter', 'database_patterns', 'tags',
        }, server_fields)
        self.assertNotIn('changes', server_fields)

    def test_gds_operations_require_a_bound_external_surface_digest(self):
        adapter, connector = client()
        self.assertFalse(adapter.supports_admin_operation(
            'graph-projection', 'create'
        ))
        qualified = Neo4jClient(
            lambda *_args: SecretLease(),
            SimpleNamespace(
                __version__='6.3.0',
                GraphDatabase=SimpleNamespace(driver=connector),
            ),
            gds_surface_sha256=QUALIFIED_GDS_SHA256,
        )
        self.assertTrue(qualified.supports_admin_operation(
            'graph-projection', 'create'
        ))
        statement, parameters = qualified._graph_projection_command(
            'create', {
                'name': 'recommendations',
                'node_projection': ['Person', 'Product'],
                'relationship_projection': {
                    'PURCHASED': {'orientation': 'NATURAL'},
                },
                'configuration': {'readConcurrency': 2},
            }, {},
        )
        self.assertEqual(
            'CALL gds.graph.project($graph_name, $node_projection, '
            '$relationship_projection, $configuration)', statement,
        )
        self.assertEqual('recommendations', parameters['graph_name'])
        self.assertEqual(
            {'readConcurrency': 2}, parameters['configuration']
        )
        with self.assertRaisesRegex(Neo4jClientError, 'does not match'):
            Neo4jClient(
                lambda *_args: SecretLease(),
                SimpleNamespace(
                    __version__='6.3.0',
                    GraphDatabase=SimpleNamespace(driver=connector),
                ),
                gds_surface_sha256='a' * 64,
            )

    def test_production_factory_binds_the_qualified_gds_surface(self):
        provider = create_provider(context(), Permissions())
        self.assertTrue(provider.client.supports_admin_operation(
            'graph-projection', 'create'
        ))

    def test_query_plan_workspace_is_bounded_and_parameterized(self):
        adapter, _connector = client()
        statement, parameters = adapter._operational_command(
            'query-plan', 'execute', {
                'source': 'MATCH (n) WHERE n.name = $name RETURN n',
                'parameters': {'name': 'Ada'},
                'mode': 'explain',
            }, {'workspace': True},
        )
        self.assertEqual(
            'EXPLAIN MATCH (n) WHERE n.name = $name RETURN n', statement
        )
        self.assertEqual({'name': 'Ada'}, parameters)
        plan = SimpleNamespace(
            operator_type='ProduceResults', identifiers=['n'],
            arguments={'EstimatedRows': 1.0}, children=[SimpleNamespace(
                operator_type='AllNodesScan', identifiers=['n'],
                arguments={}, children=[],
            )],
        )
        summary = adapter._summary(SimpleNamespace(
            counters=None, query_type='r', result_available_after=1,
            result_consumed_after=2, plan=plan, profile=None,
        ))
        self.assertEqual(
            'ProduceResults', summary['query_plan']['operator_type']
        )
        self.assertEqual(
            'AllNodesScan',
            summary['query_plan']['children'][0]['operator_type'],
        )

    def test_procedure_workspace_uses_bounded_positional_parameters(self):
        adapter, _connector = client()
        statement, parameters = adapter._operational_command(
            'procedure', 'execute', {
                'action': 'execute', 'arguments': ['Person', 10],
            }, {'name': 'db.index.vector.queryNodes'},
        )
        self.assertEqual(
            'CALL `db`.`index`.`vector`.`queryNodes`('
            '$argument_0, $argument_1)', statement,
        )
        self.assertEqual(
            {'argument_0': 'Person', 'argument_1': 10}, parameters
        )

    def test_manifest_records_exact_full_graph_qualification(self):
        manifest = json.loads((
            WEB / 'pgadmin/cdeadmin/providers/neo4j/provider_manifest.json'
        ).read_text(encoding='utf-8'))
        self.assertTrue(manifest['enabled'])
        self.assertTrue(manifest['production_registration'])
        self.assertEqual('experimental', manifest['support_state'])
        self.assertEqual(
            'passed_full_graph_object_activation',
            manifest['provenance']['activation_gate'],
        )
        self.assertEqual(
            '11_of_11_concepts_and_31_of_31_operations_live',
            manifest['provenance']['object_experience_state'],
        )
        self.assertEqual(
            'cde-neo4j-object-live:neo4j-2026.04.0-full:20260904',
            manifest['provenance']['live_evidence_reference'],
        )


if __name__ == '__main__':
    unittest.main()
