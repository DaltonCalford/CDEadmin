##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Endpoint-profile registration workflow tests."""

from __future__ import annotations

import json
import sys
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.endpoints import (  # noqa: E402
    EndpointRegistrationError,
    active_secret_fields,
    EndpointService,
    CONNECTION_CAPABILITY_CATEGORIES,
    ConnectionCapabilityError,
    RouteHealthRegistry,
    assert_connection_capabilities_complete,
    default_registration_profile,
    provider_route_form_values,
    provider_route_options,
    provider_secret_values,
    registration_profile,
    registration_interface,
    registration_interfaces,
    registration_profile_for_endpoint,
    registration_profiles,
)
from pgadmin.cdeadmin.endpoints.profiles import (  # noqa: E402
    _connection_fields,
    _secret_fields,
)
from pgadmin.cdeadmin.security import encode_credential_bundle  # noqa: E402


class RegistrationProfileTests(unittest.TestCase):

    def test_only_active_builtin_profiles_are_selectable(self):
        profiles = registration_profiles()
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
            {item['profile_id'] for item in profiles},
        )
        self.assertEqual(
            'postgresql-native', default_registration_profile()['profile_id']
        )
        network = [
            item for item in profiles if item['route_kind'] == 'network'
        ]
        embedded = [
            item for item in profiles
            if item['route_kind'] == 'embedded_file'
        ]
        self.assertTrue(all(item['default_port'] > 0 for item in network))
        self.assertTrue(all(item['default_port'] is None for item in embedded))
        self.assertTrue(all(not item['requires_secret'] for item in embedded))

    def test_unknown_profile_fails_closed(self):
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'not active'
        ):
            registration_profile('not-an-active-profile')

    def test_yugabytedb_exposes_both_native_interfaces(self):
        interfaces = registration_interfaces('yugabytedb')
        self.assertEqual({'ysql', 'ycql'}, {
            item['interface_id'] for item in interfaces
        })
        self.assertEqual({'postgresql_wire', 'cql'}, {
            item['protocol_id'] for item in interfaces
        })
        ysql = registration_interface('yugabytedb', 'ysql')
        ycql = registration_interface('yugabytedb', 'ycql')
        self.assertEqual('yugabytedb-native', ysql['profile_id'])
        self.assertEqual('yugabytedb-ycql', ycql['profile_id'])
        self.assertEqual(5433, ysql['default_port'])
        self.assertEqual(9042, ycql['default_port'])
        self.assertIn('(YSQL)', ysql['display_name'])
        self.assertIn('(YCQL)', ycql['display_name'])
        self.assertTrue(ysql['explicit_interface'])
        self.assertTrue(ycql['explicit_interface'])

    def test_wire_field_sets_are_explicitly_expanded_per_provider(self):
        ysql = registration_profile('yugabytedb-native')
        mysql = registration_profile('mysql-native')
        mariadb = registration_profile('mariadb-native')
        self.assertTrue({'sslmode', 'target_session_attrs', 'krbsrvname'} <= {
            field['route_key'] for field in ysql['connection_fields']
        })
        self.assertTrue({'auth_plugin', 'failover', 'pool_size'} <= {
            field['route_key'] for field in mysql['connection_fields']
        })
        self.assertTrue({'plugin_dir', 'reconnect', 'tls_version'} <= {
            field['route_key'] for field in mariadb['connection_fields']
        })
        self.assertEqual(3, len(mysql['secret_fields']))
        self.assertEqual(3, len(ysql['secret_fields']))

    def test_engine_interface_resolution_never_falls_back(self):
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'interface is not active'
        ):
            registration_interface('yugabytedb', 'postgresql')
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'interface is not active'
        ):
            registration_interface('postgresql', 'ysql')

    def test_catalog_is_defensive_and_implementation_neutral(self):
        profiles = registration_profiles()
        profiles[0]['display_name'] = 'changed'
        fresh = registration_profiles()
        self.assertNotEqual('changed', fresh[0]['display_name'])
        serialized = json.dumps(fresh).casefold()
        self.assertNotIn('emulation', serialized)
        self.assertNotIn('server_implementation', serialized)

    def test_every_profile_exposes_fail_closed_capability_contract(self):
        complete = []
        for profile in registration_profiles():
            declaration = profile['connection_capabilities']
            self.assertEqual(
                set(CONNECTION_CAPABILITY_CATEGORIES),
                set(declaration['categories']),
            )
            if declaration['complete']:
                complete.append(profile['profile_id'])
        self.assertEqual(
            [
                'apache-ignite-native', 'cassandra-native',
                'clickhouse-native', 'cockroachdb-native', 'dolt-native',
                'duckdb-native', 'firebird-native', 'foundationdb-native',
                'immudb-native', 'influxdb-native', 'mariadb-native',
                'milvus-native', 'mongodb-native', 'mysql-native',
                'neo4j-native', 'opensearch-native',
                'opensearch-sql-ppl', 'postgresql-native', 'redis-native',
                'sqlite-native', 'tidb-native', 'tikv-native',
                'vitess-native', 'xtdb-native',
                'yugabytedb-native', 'yugabytedb-ycql',
            ],
            sorted(complete),
        )

    def test_completion_assertion_reports_omitted_categories(self):
        with self.assertRaisesRegex(
            ConnectionCapabilityError, 'authentication.*state_visibility'
        ):
            assert_connection_capabilities_complete(
                None, 'test-native'
            )

    def test_unavailable_provider_cannot_fall_back_to_postgresql(self):
        endpoint = SimpleNamespace(
            profile_id='disabled-native',
            profile_version='1.0.0',
            provider_id='org.example.disabled',
            provider_version='1.0.0',
            experience_family='disabled',
            target_adapter_id='disabled-client',
            target_adapter_version='1.0.0',
        )
        profile = registration_profile_for_endpoint(endpoint)
        self.assertEqual('provider_endpoint', profile['workflow'])
        self.assertFalse(profile['available'])

    def test_mongodb_advanced_route_fields_round_trip_without_secrets(self):
        profile = registration_profile('mongodb-native')
        route = provider_route_options(profile, {
            'cde_route_auth_source': 'admin',
            'cde_route_replica_set': 'cdeadmin-rs',
            'cde_route_direct_connection': False,
            'cde_route_tls': True,
            'cde_route_tls_ca_file': '/certificates/ca.pem',
            'cde_route_tls_certificate_key_file': '/certificates/client.pem',
            'cde_route_connect_timeout_ms': 7000,
            'cde_route_tool_workspace': '/srv/cdeadmin/mongodb-tools',
        }, {'host': 'mongodb.example.test', 'port': 27017})
        self.assertEqual('admin', route['auth_source'])
        self.assertEqual('cdeadmin-rs', route['replica_set'])
        self.assertTrue(route['tls'])
        self.assertNotIn('password', route)
        projected = provider_route_form_values(profile, route)
        self.assertEqual('admin', projected['cde_route_auth_source'])
        self.assertEqual(
            '/certificates/ca.pem', projected['cde_route_tls_ca_file']
        )

    def test_cassandra_topology_tls_and_consistency_fields_round_trip(self):
        profile = registration_profile('cassandra-native')
        route = provider_route_options(profile, {
            'cde_route_contact_points': 'node2.example,node3.example',
            'cde_route_local_dc': 'datacenter1',
            'cde_route_tls_mode': 'self-signed',
            'cde_route_tls_ca_file': '/certificates/cassandra-ca.pem',
            'cde_route_consistency': 'LOCAL_QUORUM',
            'cde_route_serial_consistency': 'LOCAL_SERIAL',
            'cde_route_jmx_port': 7199,
            'cde_route_tool_workspace': '/srv/cdeadmin/cassandra-tools',
        }, {'host': 'node1.example', 'port': 9042})
        self.assertEqual('self-signed', route['tls_mode'])
        self.assertEqual('LOCAL_QUORUM', route['consistency'])
        self.assertEqual(
            '/certificates/cassandra-ca.pem', route['tls_ca_file']
        )
        self.assertEqual(
            'node2.example,node3.example', route['contact_points']
        )
        self.assertNotIn('password', route)
        projected = provider_route_form_values(profile, route)
        self.assertEqual(
            '/srv/cdeadmin/cassandra-tools',
            projected['cde_route_tool_workspace'],
        )

    def test_advanced_route_fields_fail_closed(self):
        profile = registration_profile('mongodb-native')
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'undeclared'
        ):
            provider_route_options(profile, {
                'cde_route_inline_password': 'must-not-be-admitted',
            })
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'admitted range'
        ):
            provider_route_options(profile, {
                'cde_route_connect_timeout_ms': 1,
            })
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'true or false'
        ):
            provider_route_options(profile, {
                'cde_route_tls': 'yes',
            })

    def test_select_connection_field_is_normalized_and_enforced(self):
        fields = _connection_fields({'connection_fields': [{
            'field_id': 'tls_mode',
            'label': 'TLS mode',
            'control': 'select',
            'default': 'disabled',
            'options': [
                {'value': 'disabled', 'label': 'Disabled'},
                {'value': 'system-ca', 'label': 'System CA'},
            ],
        }]})
        profile = {'connection_fields': fields}
        self.assertEqual(
            {'tls_mode': 'system-ca'},
            provider_route_options(
                profile, {'cde_route_tls_mode': 'system-ca'}
            ),
        )
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'not an admitted option'
        ):
            provider_route_options(
                profile, {'cde_route_tls_mode': 'trust-everything'}
            )

    def test_select_connection_field_rejects_invalid_manifest(self):
        base = {
            'field_id': 'tls_mode',
            'label': 'TLS mode',
            'control': 'select',
            'default': 'disabled',
        }
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'must not be empty'
        ):
            _connection_fields({
                'connection_fields': [{**base, 'options': []}],
            })
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'default is invalid'
        ):
            _connection_fields({'connection_fields': [{
                **base,
                'options': [{'value': 'system-ca', 'label': 'System CA'}],
            }]})

    def test_conditional_fields_are_removed_and_fail_closed(self):
        fields = _connection_fields({'connection_fields': [
            {
                'field_id': 'auth_kind', 'label': 'Authentication',
                'control': 'select', 'default': 'none',
                'options': [
                    {'value': 'none', 'label': 'None'},
                    {'value': 'basic', 'label': 'Basic'},
                ],
            },
            {
                'field_id': 'username', 'label': 'Username',
                'control': 'text',
                'visible_when': {
                    'field_id': 'auth_kind', 'equals': 'basic',
                },
            },
        ]})
        profile = {'connection_fields': fields}
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'unavailable for this selection'
        ):
            provider_route_options(profile, {
                'cde_route_auth_kind': 'none',
                'cde_route_username': 'hidden-user',
            })
        self.assertEqual(
            {'auth_kind': 'none'},
            provider_route_options(
                profile, {'cde_route_auth_kind': 'none'},
                {'username': 'stale-user'},
            ),
        )

    def test_fractional_number_and_cross_field_requirements(self):
        fields = _connection_fields({'connection_fields': [
            {
                'field_id': 'certificate', 'label': 'Certificate',
                'control': 'file', 'requires_fields': ['key'],
            },
            {
                'field_id': 'key', 'label': 'Key', 'control': 'file',
                'requires_fields': ['certificate'],
            },
            {
                'field_id': 'timeout', 'label': 'Timeout',
                'control': 'number', 'integer': False,
                'minimum': 0, 'maximum': 60,
            },
        ]})
        profile = {'connection_fields': fields}
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'Certificate requires: key'
        ):
            provider_route_options(profile, {
                'cde_route_certificate': '/cert.pem',
            })
        self.assertEqual(0.25, provider_route_options(profile, {
            'cde_route_timeout': 0.25,
        })['timeout'])

    def test_json_connection_field_is_parsed_and_projected_canonically(self):
        fields = _connection_fields({'connection_fields': [{
            'field_id': 'properties', 'label': 'Properties',
            'control': 'json',
        }]})
        profile = {'connection_fields': fields}
        route = provider_route_options(profile, {
            'cde_route_properties': '{"z":2,"a":1}',
        })
        self.assertEqual({'a': 1, 'z': 2}, route['properties'])
        self.assertEqual(
            '{"a":1,"z":2}',
            provider_route_form_values(
                profile, route
            )['cde_route_properties'],
        )
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'must be valid JSON'
        ):
            provider_route_options(profile, {
                'cde_route_properties': '{invalid',
            })

    def test_secret_fields_follow_non_secret_mechanism_selection(self):
        connection_fields = _connection_fields({'connection_fields': [{
            'field_id': 'auth_kind', 'label': 'Authentication',
            'control': 'select', 'default': 'none',
            'options': [
                {'value': 'none', 'label': 'None'},
                {'value': 'basic', 'label': 'Basic'},
                {'value': 'bearer', 'label': 'Bearer'},
            ],
        }]})
        secret_fields = _secret_fields({
            'secret_fields': [
                {
                    'field_id': 'password',
                    'secret_kind': 'database_password',
                    'label': 'Password', 'primary': True,
                    'visible_when': {
                        'field_id': 'auth_kind', 'equals': 'basic',
                    },
                },
                {
                    'field_id': 'token', 'secret_kind': 'api_token',
                    'label': 'Token', 'visible_when': {
                        'field_id': 'auth_kind', 'equals': 'bearer',
                    },
                },
            ],
        }, connection_fields)
        profile = {
            'connection_fields': connection_fields,
            'secret_fields': secret_fields,
        }
        self.assertEqual(
            ['database_password'],
            [item['secret_kind'] for item in active_secret_fields(
                profile, {'auth_kind': 'basic'}
            )],
        )
        self.assertEqual(
            ['api_token'],
            [item['secret_kind'] for item in active_secret_fields(
                profile, {'auth_kind': 'bearer'}
            )],
        )
        self.assertEqual([], active_secret_fields(
            profile, {'auth_kind': 'none'}
        ))
        self.assertEqual(
            {'api_token': 'token-canary'},
            provider_secret_values(
                profile, {'auth_kind': 'bearer'},
                {'cde_secret_token': 'token-canary'},
            ),
        )
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'unavailable for this selection'
        ):
            provider_secret_values(
                profile, {'auth_kind': 'none'},
                {'cde_secret_password': 'hidden-canary'},
            )


class EndpointVerificationTests(unittest.TestCase):

    def test_protected_locator_selects_one_typed_bundle_credential(self):
        value = encode_credential_bundle({
            'database_password': 'primary-canary',
            'database_password_2': 'second-canary',
        })
        resolver_class = __import__(
            'pgadmin.cdeadmin.endpoints.service',
            fromlist=['ProtectedColumnResolver'],
        ).ProtectedColumnResolver
        self.assertEqual(
            'second-canary',
            resolver_class._select_credential(
                value, 'password', 'database_password_2'
            ),
        )

    def test_route_health_orders_ready_routes_and_resets_on_success(self):
        now = [10.0]
        registry = RouteHealthRegistry(
            clock=lambda: now[0], base_backoff=2, max_backoff=8
        )
        first = SimpleNamespace(id='route-a', priority=0)
        second = SimpleNamespace(id='route-b', priority=1)

        registry.record_failure('endpoint-a', 'route-a')
        self.assertEqual(
            ('route-b',),
            tuple(route.id for route in registry.candidates(
                'endpoint-a', [second, first]
            )),
        )
        now[0] = 12.0
        self.assertEqual(
            ('route-a', 'route-b'),
            tuple(route.id for route in registry.candidates(
                'endpoint-a', [second, first]
            )),
        )
        registry.record_success('endpoint-a', 'route-a')
        self.assertEqual(
            0,
            registry.snapshot('endpoint-a')['route-a'][
                'consecutive_failures'
            ],
        )
        registry.clear('endpoint-a', 'route-a')
        self.assertNotIn('route-a', registry.snapshot('endpoint-a'))

    def test_persistent_route_is_provider_driven_and_secret_free(self):
        security = SimpleNamespace(secrets=SimpleNamespace(
            register_resolver=lambda *_args: None,
        ))
        service = EndpointService(SimpleNamespace(), security)
        profile = registration_profile('mysql-native')
        result = service._validated_route(profile, {
            'host': 'mysql-two.example.test',
            'port': 3307,
            'user': 'operator',
            'database': 'application',
            'priority': 4,
            'cde_route_connection_timeout': 17,
            'cde_route_compress': True,
        })
        self.assertEqual('mysql-two.example.test', result['host'])
        self.assertEqual(17, result['connection_timeout'])
        self.assertTrue(result['compress'])
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'cannot contain credentials'
        ):
            service._validated_route(profile, {
                'host': 'mysql-two.example.test', 'port': 3307,
                'password': 'inline-secret',
            })

    def test_route_crud_stales_identity_and_retains_final_route(self):
        endpoint = SimpleNamespace(
            id='endpoint-one', provider_version='1.0',
            profile_id='mysql-native', routes=[SimpleNamespace(
                id='route-one', endpoint_id='endpoint-one',
                route_kind='network', route_reference='existing', priority=0,
                configuration=json.dumps({
                    'host': 'mysql-one.example.test', 'port': 3306,
                }),
            )],
            runtime_identity=SimpleNamespace(
                verification_state='verified',
                verified_runtime_family='mysql',
                verified_runtime_version='9.7.0',
                verification_evidence_reference='evidence-one',
                verified_at='now',
            ),
        )
        server = SimpleNamespace(endpoint_profile=endpoint)

        class Route:
            def __init__(self, **values):
                self.__dict__.update(values)

        class Session:
            @staticmethod
            def add(model):
                endpoint.routes.append(model)

            @staticmethod
            def delete(model):
                endpoint.routes.remove(model)

            @staticmethod
            def commit():
                return None

        model_module = ModuleType('pgadmin.model')
        model_module.EndpointRoute = Route
        model_module.db = SimpleNamespace(session=Session())
        security = SimpleNamespace(secrets=SimpleNamespace(
            register_resolver=lambda *_args: None,
        ))
        service = EndpointService(SimpleNamespace(), security)
        with patch.dict(sys.modules, {'pgadmin.model': model_module}):
            created = service.create_route(server, {
                'host': 'mysql-two.example.test', 'port': 3307,
                'priority': 1, 'cde_route_connection_timeout': 12,
            })
            route_id = created['routes'][1]['route_id']
            updated = service.update_route(server, route_id, {
                'route_id': route_id, 'priority': 2,
                'host': 'mysql-three.example.test', 'port': 3308,
            })
            self.assertEqual(
                'mysql-three.example.test',
                updated['routes'][1]['configuration']['host'],
            )
            remaining = service.delete_route(server, route_id)
            self.assertEqual(1, len(remaining['routes']))
            with self.assertRaisesRegex(
                EndpointRegistrationError, 'final endpoint route'
            ):
                service.delete_route(server, 'route-one')
        self.assertEqual('stale', endpoint.runtime_identity.verification_state)
        self.assertIsNone(endpoint.runtime_identity.verified_runtime_family)

    def test_verification_fails_over_before_session_establishment(self):
        endpoint_id = str(uuid.uuid4())
        runtime = SimpleNamespace(
            declared_runtime_family='example',
            verification_state='unverified',
        )
        routes = [
            SimpleNamespace(
                id='route-a', priority=0,
                configuration=json.dumps({'host': 'unavailable'}),
            ),
            SimpleNamespace(
                id='route-b', priority=1,
                configuration=json.dumps({'host': 'available'}),
            ),
        ]
        endpoint = SimpleNamespace(
            id=endpoint_id, endpoint_mode='legacy_native',
            experience_family='example', provider_id='org.example',
            provider_version='1.0', profile_id='example-native',
            profile_version='1.0', target_adapter_id='example',
            target_adapter_version='1.0',
            pool_namespace=str(uuid.uuid4()),
            session_namespace=str(uuid.uuid4()),
            cache_namespace=str(uuid.uuid4()),
            diagnostic_namespace=str(uuid.uuid4()), runtime_identity=runtime,
            secret_references=[], routes=routes,
        )
        server = SimpleNamespace(
            id=10, user_id=7, endpoint_profile=endpoint
        )
        observed = []

        class Provider:
            def discover_endpoint(self, request):
                host = request['route']['host']
                observed.append(host)
                if host == 'unavailable':
                    raise OSError('connection refused')
                return {'verified_runtime': {
                    'engine_id': 'example', 'version': '1.0',
                    'evidence_reference': 'evidence:route-b',
                }}

        security = SimpleNamespace(secrets=SimpleNamespace(
            register_resolver=lambda *_args: None,
            register_reference=lambda *_args: None,
        ))
        service = EndpointService(
            SimpleNamespace(resolve=lambda _context: SimpleNamespace(
                instance=Provider()
            )),
            security,
        )
        with patch(
            'pgadmin.cdeadmin.endpoints.service.registration_profile',
            return_value={
                'route_kind': 'network', 'requires_secret': False,
            },
        ), patch.object(service, '_record_verification'):
            result = service.verify_server(server)

        self.assertEqual(['unavailable', 'available'], observed)
        self.assertEqual('route-b', result['selected_route_id'])
        health = service.route_health.snapshot(endpoint_id)
        self.assertEqual(1, health['route-a']['consecutive_failures'])
        self.assertEqual(0, health['route-b']['consecutive_failures'])

    def test_optional_bearer_auth_uses_typed_api_token_reference(self):
        endpoint_id = str(uuid.uuid4())
        route = SimpleNamespace(
            id=str(uuid.uuid4()), priority=0,
            configuration=json.dumps({
                'host': 'influx.example.test', 'port': 8181,
                'auth_kind': 'bearer',
            }),
        )
        reference = SimpleNamespace(
            id=str(uuid.uuid4()), secret_kind='api_token',
            storage_kind='legacy_protected_column',
            secret_reference='server:10:password',
        )
        endpoint = SimpleNamespace(
            id=endpoint_id, endpoint_mode='legacy_native',
            secret_references=[reference], routes=[route],
        )
        server = SimpleNamespace(user_id=7)
        observed = []
        security = SimpleNamespace(secrets=SimpleNamespace(
            register_resolver=lambda *_args: None,
            register_reference=observed.append,
        ))
        service = EndpointService(SimpleNamespace(), security)

        result, bound = service._route_and_reference(
            server, endpoint, {
                'requires_secret': False, 'supports_secret': True,
            }
        )

        self.assertEqual(reference.id, result['credential_reference_id'])
        self.assertEqual('api_token', result['credential_kind'])
        self.assertEqual('api_token', bound.secret_kind)
        self.assertEqual([bound], observed)

    def test_embedded_route_does_not_require_or_invent_a_secret(self):
        route = SimpleNamespace(
            id=str(uuid.uuid4()), priority=0,
            configuration=json.dumps({
                'database': '/srv/cdeadmin/example.sqlite',
                'filesystem_root': '/srv/cdeadmin',
            }),
        )
        endpoint = SimpleNamespace(secret_references=[], routes=[route])
        server = SimpleNamespace(user_id=7)
        security = SimpleNamespace(secrets=SimpleNamespace(
            register_resolver=lambda *_args: None,
        ))
        service = EndpointService(SimpleNamespace(), security)

        result, reference = service._route_and_reference(
            server, endpoint, requires_secret=False
        )

        self.assertIsNone(reference)
        self.assertEqual(route.id, result['route_id'])
        self.assertNotIn('credential_reference_id', result)
        self.assertNotIn('principal_reference', result)

    def test_verification_route_contains_reference_but_no_secret(self):
        endpoint_id = str(uuid.uuid4())
        reference_id = str(uuid.uuid4())
        runtime = SimpleNamespace(
            declared_runtime_family='example',
            verification_state='unverified',
        )
        route = SimpleNamespace(
            id=str(uuid.uuid4()), priority=0,
            configuration=json.dumps({
                'host': 'endpoint.example.test',
                'port': 1234,
                'user': 'endpoint_user',
                'database': 'endpoint_database',
            }),
        )
        reference = SimpleNamespace(
            id=reference_id,
            secret_kind='database_password',
            storage_kind='legacy_protected_column',
            secret_reference='server:10:password',
        )
        endpoint = SimpleNamespace(
            id=endpoint_id,
            endpoint_mode='legacy_native',
            experience_family='example',
            provider_id='org.example.provider',
            provider_version='1.0.0',
            profile_id='example-native',
            profile_version='2.0.0',
            target_adapter_id='example-client',
            target_adapter_version='3.0.0',
            pool_namespace=str(uuid.uuid4()),
            session_namespace=str(uuid.uuid4()),
            cache_namespace=str(uuid.uuid4()),
            diagnostic_namespace=str(uuid.uuid4()),
            runtime_identity=runtime,
            secret_references=[reference],
            routes=[route],
        )
        server = SimpleNamespace(
            id=10, user_id=7, endpoint_profile=endpoint
        )
        observed = {}

        class Secrets:
            def register_resolver(self, resolver_id, resolver):
                observed['resolver'] = (resolver_id, resolver)

            def register_reference(self, value):
                observed['reference'] = value

        class Provider:
            def discover_endpoint(self, request):
                observed['request'] = request
                return {'verified_runtime': {
                    'engine_id': 'example',
                    'version': '2.0.0',
                    'evidence_reference': 'evidence:example',
                }}

        registry = SimpleNamespace(resolve=lambda context: SimpleNamespace(
            instance=Provider()
        ))
        security = SimpleNamespace(secrets=Secrets())
        service = EndpointService(registry, security)
        with patch(
            'pgadmin.cdeadmin.endpoints.service.registration_profile',
            return_value={
                'route_kind': 'network', 'requires_secret': True,
            },
        ), patch.object(service, '_record_verification') as record:
            result = service.verify_server(server, 'secret-canary')

        serialized = json.dumps(observed['request'])
        self.assertNotIn('secret-canary', serialized)
        request_route = observed['request']['route']
        self.assertEqual(reference_id, request_route[
            'credential_reference_id'
        ])
        self.assertEqual('user:7', request_route['principal_reference'])
        self.assertEqual('verified', result['verification_state'])
        record.assert_called_once()
        self.assertEqual({}, service.resolver._transient)


if __name__ == '__main__':
    unittest.main()
