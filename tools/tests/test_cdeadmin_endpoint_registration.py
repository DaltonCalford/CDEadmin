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
    EndpointService,
    default_registration_profile,
    provider_route_form_values,
    provider_route_options,
    registration_profile,
    registration_profile_for_endpoint,
    registration_profiles,
)
from pgadmin.cdeadmin.endpoints.profiles import _connection_fields  # noqa: E402


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

    def test_catalog_is_defensive_and_implementation_neutral(self):
        profiles = registration_profiles()
        profiles[0]['display_name'] = 'changed'
        fresh = registration_profiles()
        self.assertNotEqual('changed', fresh[0]['display_name'])
        serialized = json.dumps(fresh).casefold()
        self.assertNotIn('emulation', serialized)
        self.assertNotIn('server_implementation', serialized)

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


class EndpointVerificationTests(unittest.TestCase):

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
