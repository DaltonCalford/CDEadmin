##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Reusable, evidence-bearing connection contracts for shared clients."""

from __future__ import annotations

import copy


def _complete(features, *evidence):
    return {
        'state': 'complete',
        'features': list(features),
        'evidence': list(evidence),
    }


def _na(reason, *evidence):
    return {
        'state': 'not_applicable',
        'features': [],
        'evidence': list(evidence),
        'reason': reason,
    }


PROFILE_EVIDENCE = 'unit:test_cdeadmin_endpoint_registration'
ROUTE_EVIDENCE = 'unit:test_cdeadmin_endpoint_persistence'


CQL = {
    'authentication': _complete(
        ('none', 'username-password', 'mutual-TLS',
         'username-password-with-mutual-TLS'),
        'unit:test_cdeadmin_cassandra_provider',
    ),
    'tls': _complete(
        ('disabled', 'system-CA', 'private-CA', 'self-signed',
         'hostname-verification', 'minimum-TLS-version', 'cipher-policy'),
        'unit:test_cdeadmin_cassandra_provider',
    ),
    'client_certificates': _complete(
        ('certificate-and-key', 'encrypted-private-key'),
        'unit:test_cdeadmin_cassandra_provider',
        'unit:test_cdeadmin_credential_bundle',
    ),
    'connection_profiles': _complete(
        ('persistent-provider-profile', 'editable-connection-options',
         'typed-secret-bundle', 'generation-based-runtime-invalidation'),
        PROFILE_EVIDENCE, ROUTE_EVIDENCE,
    ),
    'multiple_endpoints': _complete(
        ('contact-point-list', 'persistent-prioritized-routes'),
        'unit:test_cdeadmin_cassandra_provider', ROUTE_EVIDENCE,
    ),
    'topology_discovery': _complete(
        ('driver-cluster-metadata', 'system-topology-catalog'),
        'unit:test_cdeadmin_cassandra_provider',
    ),
    'routing_failover': _complete(
        ('token-aware-routing', 'datacenter-aware-routing',
         'round-robin-routing', 'bounded-pre-session-route-failover'),
        'unit:test_cdeadmin_cassandra_provider', ROUTE_EVIDENCE,
    ),
    'session_defaults': _complete(
        ('initial-keyspace', 'application-name', 'request-default-timeout'),
        'unit:test_cdeadmin_cassandra_provider',
    ),
    'timeouts': _complete(
        ('connect', 'control-connection', 'request', 'heartbeat',
         'schema-agreement'),
        'unit:test_cdeadmin_cassandra_provider',
    ),
    'compression': _complete(
        ('none', 'LZ4', 'Snappy'),
        'unit:test_cdeadmin_cassandra_provider',
    ),
    'consistency_isolation': _complete(
        ('default-consistency', 'serial-consistency'),
        'unit:test_cdeadmin_cassandra_provider',
    ),
    'pooling': _complete(
        ('driver-managed-per-host-pools', 'executor-thread-limit'),
        'unit:test_cdeadmin_cassandra_provider',
    ),
    'reconnection': _complete(
        ('exponential-reconnection-policy', 'bounded-attempts',
         'heartbeat-liveness'),
        'unit:test_cdeadmin_cassandra_provider',
    ),
    'state_visibility': _complete(
        ('session-state', 'cluster-metadata', 'transaction-finality-opaque'),
        'unit:test_cdeadmin_cassandra_provider',
    ),
}


REDIS = {
    'authentication': _complete(
        ('none', 'default-user-password', 'ACL-username-password',
         'independent-Sentinel-ACL'),
        'unit:test_cdeadmin_redis_provider',
    ),
    'tls': _complete(
        ('disabled', 'system-CA', 'private-CA', 'self-signed',
         'hostname-verification', 'minimum-TLS-version', 'cipher-policy',
         'OCSP-validation'),
        'unit:test_cdeadmin_redis_provider',
    ),
    'client_certificates': _complete(
        ('certificate-and-key', 'encrypted-private-key'),
        'unit:test_cdeadmin_redis_provider',
        'unit:test_cdeadmin_credential_bundle',
    ),
    'connection_profiles': _complete(
        ('persistent-provider-profile', 'editable-connection-options',
         'typed-secret-bundle', 'generation-based-runtime-invalidation'),
        PROFILE_EVIDENCE, ROUTE_EVIDENCE,
    ),
    'multiple_endpoints': _complete(
        ('startup-node-list', 'Sentinel-list',
         'persistent-prioritized-routes'),
        'unit:test_cdeadmin_redis_provider', ROUTE_EVIDENCE,
    ),
    'topology_discovery': _complete(
        ('standalone-replica', 'Sentinel', 'Redis-Cluster',
         'dynamic-cluster-startup-nodes'),
        'unit:test_cdeadmin_redis_provider',
    ),
    'routing_failover': _complete(
        ('Sentinel-primary-or-replica', 'cluster-slot-routing',
         'full-coverage-policy', 'bounded-pre-session-route-failover'),
        'unit:test_cdeadmin_redis_provider', ROUTE_EVIDENCE,
    ),
    'session_defaults': _complete(
        ('logical-database', 'RESP3', 'client-name', 'read-replica-mode'),
        'unit:test_cdeadmin_redis_provider',
    ),
    'timeouts': _complete(
        ('connect', 'command', 'health-check'),
        'unit:test_cdeadmin_redis_provider',
    ),
    'compression': _na(
        'RESP does not define negotiated transport compression.',
        'unit:test_cdeadmin_redis_provider',
    ),
    'consistency_isolation': _na(
        'Redis connections expose command and topology semantics, not a '
        'client-selectable transaction isolation or consistency level.',
        'unit:test_cdeadmin_redis_provider',
    ),
    'pooling': _complete(
        ('driver-connection-pool', 'maximum-connections',
         'health-check', 'generation-based-pool-retirement'),
        'unit:test_cdeadmin_redis_provider',
        'unit:test_cdeadmin_provider_registry',
    ),
    'reconnection': _complete(
        ('zero-automatic-mutation-retry', 'Sentinel-rediscovery',
         'cluster-topology-refresh', 'bounded-pre-session-route-failover'),
        'unit:test_cdeadmin_redis_provider', ROUTE_EVIDENCE,
    ),
    'state_visibility': _complete(
        ('connection-topology', 'server-runtime',
         'transaction-finality-opaque'),
        'unit:test_cdeadmin_redis_provider',
    ),
}


NEO4J = {
    'authentication': _complete(
        ('none', 'basic', 'Kerberos', 'bearer', 'custom-token'),
        'unit:test_cdeadmin_neo4j_provider',
    ),
    'tls': _complete(
        ('disabled', 'system-CA', 'private-CA', 'self-signed'),
        'unit:test_cdeadmin_neo4j_provider',
    ),
    'client_certificates': _complete(
        ('certificate-and-key', 'encrypted-private-key'),
        'unit:test_cdeadmin_neo4j_provider',
        'unit:test_cdeadmin_credential_bundle',
    ),
    'connection_profiles': _complete(
        ('persistent-provider-profile', 'editable-connection-options',
         'typed-secret-bundle', 'generation-based-runtime-invalidation'),
        PROFILE_EVIDENCE, ROUTE_EVIDENCE,
    ),
    'multiple_endpoints': _complete(
        ('custom-address-resolver', 'persistent-prioritized-routes'),
        'unit:test_cdeadmin_neo4j_provider', ROUTE_EVIDENCE,
    ),
    'topology_discovery': _complete(
        ('neo4j-routing-driver', 'server-info', 'database-catalog'),
        'unit:test_cdeadmin_neo4j_provider',
    ),
    'routing_failover': _complete(
        ('routing-URI', 'direct-Bolt', 'custom-resolver',
         'bounded-transaction-retry-window',
         'bounded-pre-session-route-failover'),
        'unit:test_cdeadmin_neo4j_provider', ROUTE_EVIDENCE,
    ),
    'session_defaults': _complete(
        ('database', 'read-write-access-mode', 'fetch-size', 'impersonation',
         'initial-bookmarks', 'notification-severity'),
        'unit:test_cdeadmin_neo4j_provider',
    ),
    'timeouts': _complete(
        ('connect', 'pool-acquisition', 'transaction-retry-window',
         'idle-liveness-check'),
        'unit:test_cdeadmin_neo4j_provider',
    ),
    'compression': _na(
        'The qualified Neo4j Python driver exposes no Bolt compression '
        'connection setting.',
        'unit:test_cdeadmin_neo4j_provider',
    ),
    'consistency_isolation': _complete(
        ('read-write-access-mode', 'causal-bookmarks'),
        'unit:test_cdeadmin_neo4j_provider',
    ),
    'pooling': _complete(
        ('maximum-size', 'connection-lifetime', 'acquisition-timeout',
         'liveness-check', 'generation-based-pool-retirement'),
        'unit:test_cdeadmin_neo4j_provider',
        'unit:test_cdeadmin_provider_registry',
    ),
    'reconnection': _complete(
        ('driver-routing-recovery', 'bounded-transaction-retry-window',
         'bounded-pre-session-route-failover'),
        'unit:test_cdeadmin_neo4j_provider', ROUTE_EVIDENCE,
    ),
    'state_visibility': _complete(
        ('database', 'session-state', 'server-agent', 'Bolt-version',
         'transaction-finality-opaque'),
        'unit:test_cdeadmin_neo4j_provider',
    ),
}


MYSQL = {
    'authentication': _complete(
        ('server-selected-plugin', 'native-password', 'caching-SHA2',
         'SHA256-password', 'Kerberos', 'OCI-IAM', 'WebAuthn',
         'OpenID-Connect', 'multi-factor-passwords'),
        'unit:test_cdeadmin_relational_providers',
    ),
    'tls': _complete(
        ('disabled-or-required', 'CA-validation', 'identity-validation',
         'TLS-version-policy', 'cipher-suite-policy'),
        'unit:test_cdeadmin_relational_providers',
    ),
    'client_certificates': _complete(
        ('certificate-and-key',),
        'unit:test_cdeadmin_relational_providers',
    ),
    'connection_profiles': _complete(
        ('persistent-provider-profile', 'editable-connection-options',
         'typed-multi-factor-secret-bundle',
         'generation-based-runtime-invalidation'),
        PROFILE_EVIDENCE, ROUTE_EVIDENCE,
    ),
    'multiple_endpoints': _complete(
        ('connector-failover-list', 'DNS-SRV',
         'persistent-prioritized-routes'),
        'unit:test_cdeadmin_relational_providers', ROUTE_EVIDENCE,
    ),
    'topology_discovery': _complete(
        ('DNS-SRV-discovery', 'connector-failover-candidates'),
        'unit:test_cdeadmin_relational_providers',
    ),
    'routing_failover': _complete(
        ('connector-failover', 'DNS-SRV',
         'bounded-pre-session-route-failover'),
        'unit:test_cdeadmin_relational_providers', ROUTE_EVIDENCE,
    ),
    'session_defaults': _complete(
        ('character-set', 'collation', 'time-zone', 'SQL-mode',
         'autocommit', 'connection-attributes', 'initial-command'),
        'unit:test_cdeadmin_relational_providers',
    ),
    'timeouts': _complete(
        ('connect', 'read', 'write'),
        'unit:test_cdeadmin_relational_providers',
    ),
    'compression': _complete(
        ('classic-protocol-compression',),
        'unit:test_cdeadmin_relational_providers',
    ),
    'consistency_isolation': _complete(
        ('autocommit', 'typed-session-transaction-isolation'),
        'unit:test_cdeadmin_relational_providers',
    ),
    'pooling': _complete(
        ('native-connector-pool', 'pool-size', 'session-reset',
         'generation-based-pool-retirement'),
        'unit:test_cdeadmin_relational_providers',
        'unit:test_cdeadmin_provider_registry',
    ),
    'reconnection': _complete(
        ('connector-failover-at-connect',
         'bounded-pre-session-route-failover',
         'explicit-new-session-after-loss'),
        'unit:test_cdeadmin_relational_providers', ROUTE_EVIDENCE,
    ),
    'state_visibility': _complete(
        ('autocommit', 'in-transaction', 'isolation-level',
         'provider-owned-finality'),
        'unit:test_cdeadmin_relational_providers',
    ),
}


MARIADB = copy.deepcopy(MYSQL)
MARIADB.update({
    'authentication': _complete(
        ('server-selected-plugin', 'password', 'Unix-socket',
         'connector-plugin-directory', 'client-option-file'),
        'unit:test_cdeadmin_relational_providers',
    ),
    'multiple_endpoints': _complete(
        ('Connector-C-host-list', 'persistent-prioritized-routes'),
        'unit:test_cdeadmin_relational_providers', ROUTE_EVIDENCE,
    ),
    'topology_discovery': _na(
        'MariaDB Connector/Python accepts explicit host candidates but does '
        'not expose a client topology-discovery service.',
        'unit:test_cdeadmin_relational_providers',
    ),
    'routing_failover': _complete(
        ('Connector-C-host-failover', 'automatic-driver-reconnect-option',
         'bounded-pre-session-route-failover'),
        'unit:test_cdeadmin_relational_providers', ROUTE_EVIDENCE,
    ),
    'session_defaults': _complete(
        ('autocommit', 'initial-and-reconnect-command',
         'client-option-file-and-group'),
        'unit:test_cdeadmin_relational_providers',
    ),
    'pooling': _complete(
        ('native-ConnectionPool', 'pool-size', 'session-reset',
         'validation-interval', 'generation-based-pool-retirement'),
        'unit:test_cdeadmin_relational_providers',
        'unit:test_cdeadmin_provider_registry',
    ),
})


FIREBIRD = {
    'authentication': _complete(
        ('username-password', 'trusted-authentication',
         'ordered-authentication-plugin-list'),
        'unit:test_cdeadmin_relational_providers',
    ),
    'tls': _na(
        'Firebird native wire transport uses WireCrypt plug-ins rather than '
        'TLS configuration.',
        'unit:test_cdeadmin_relational_providers',
    ),
    'client_certificates': _na(
        'The qualified Firebird native driver does not expose TLS client '
        'certificate authentication.',
        'unit:test_cdeadmin_relational_providers',
    ),
    'connection_profiles': _complete(
        ('persistent-provider-profile', 'editable-connection-options',
         'typed-secret-bundle', 'generation-based-runtime-invalidation'),
        PROFILE_EVIDENCE, ROUTE_EVIDENCE,
    ),
    'multiple_endpoints': _complete(
        ('persistent-prioritized-routes', 'IPv4-and-native-protocols'),
        'unit:test_cdeadmin_relational_providers', ROUTE_EVIDENCE,
    ),
    'topology_discovery': _na(
        'Firebird native attachments do not expose a cluster or replica-set '
        'discovery protocol.',
        'unit:test_cdeadmin_relational_providers',
    ),
    'routing_failover': _complete(
        ('bounded-pre-session-route-failover',
         'health-aware-prioritized-routes'),
        ROUTE_EVIDENCE,
    ),
    'session_defaults': _complete(
        ('role', 'character-set', 'time-zone', 'DBKEY-scope',
         'database-trigger-policy', 'garbage-collection-policy'),
        'unit:test_cdeadmin_relational_providers',
    ),
    'timeouts': _complete(
        ('attach-timeout', 'dummy-packet-interval',
         'transaction-lock-timeout'),
        'unit:test_cdeadmin_relational_providers',
    ),
    'compression': _complete(
        ('native-wire-compression',),
        'unit:test_cdeadmin_relational_providers',
    ),
    'consistency_isolation': _complete(
        ('six-native-isolation-modes', 'read-only-or-read-write',
         'lock-timeout'),
        'unit:test_cdeadmin_relational_providers',
    ),
    'pooling': _na(
        'The qualified firebird-driver 1.10.11 connection API does not '
        'provide a native connection-pool contract.',
        'unit:test_cdeadmin_relational_providers',
    ),
    'reconnection': _complete(
        ('bounded-pre-session-route-failover',
         'explicit-new-attachment-after-loss', 'zero-mutation-replay'),
        ROUTE_EVIDENCE, 'unit:test_cdeadmin_relational_providers',
    ),
    'state_visibility': _complete(
        ('transaction-observation', 'isolation-default',
         'provider-owned-finality'),
        'unit:test_cdeadmin_relational_providers',
    ),
}


POSTGRESQL_WIRE = {
    'authentication': _complete(
        ('server-negotiated-password-and-SCRAM', 'GSSAPI-and-SSPI',
         'OAuth', 'client-certificate', 'channel-binding',
         'required-authentication-policy'),
        'unit:test_cdeadmin_distributed_providers',
        'unit:test_cdeadmin_credential_bundle',
    ),
    'tls': _complete(
        ('disable', 'allow', 'prefer', 'require', 'verify-CA',
         'verify-full', 'protocol-version-range', 'SNI',
         'certificate-revocation', 'direct-TLS-negotiation'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'client_certificates': _complete(
        ('certificate-and-key', 'encrypted-private-key'),
        'unit:test_cdeadmin_distributed_providers',
        'unit:test_cdeadmin_credential_bundle',
    ),
    'connection_profiles': _complete(
        ('persistent-provider-profile', 'editable-connection-options',
         'typed-secret-bundle', 'generation-based-runtime-invalidation'),
        PROFILE_EVIDENCE, ROUTE_EVIDENCE,
    ),
    'multiple_endpoints': _complete(
        ('libpq-host-list', 'persistent-prioritized-routes'),
        'unit:test_cdeadmin_distributed_providers', ROUTE_EVIDENCE,
    ),
    'topology_discovery': _complete(
        ('target-session-attributes', 'host-load-balancing',
         'provider-topology-catalog'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'routing_failover': _complete(
        ('libpq-multi-host-failover', 'target-session-attributes',
         'random-host-load-balancing',
         'bounded-pre-session-route-failover'),
        'unit:test_cdeadmin_distributed_providers', ROUTE_EVIDENCE,
    ),
    'session_defaults': _complete(
        ('database', 'application-name', 'client-encoding',
         'server-options', 'autocommit'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'timeouts': _complete(
        ('connect', 'TCP-user-timeout', 'TCP-keepalive-policy',
         'pool-acquisition'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'compression': _na(
        'The PostgreSQL wire protocol and qualified libpq client expose no '
        'negotiated transport-compression setting.',
        'unit:test_cdeadmin_distributed_providers',
    ),
    'consistency_isolation': _complete(
        ('four-transaction-isolation-modes', 'read-only-or-read-write',
         'deferrable-or-not-deferrable', 'autocommit'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'pooling': _complete(
        ('optional-psycopg-pool', 'minimum-and-maximum-size',
         'acquisition-timeout', 'queue-limit', 'lifetime', 'idle-timeout',
         'checkout-validation', 'background-reconnect-window',
         'generation-based-pool-retirement'),
        'unit:test_cdeadmin_distributed_providers',
        'unit:test_cdeadmin_provider_registry',
    ),
    'reconnection': _complete(
        ('pool-background-replenishment', 'libpq-multi-host-connect',
         'bounded-pre-session-route-failover',
         'explicit-new-session-after-loss'),
        'unit:test_cdeadmin_distributed_providers', ROUTE_EVIDENCE,
    ),
    'state_visibility': _complete(
        ('autocommit', 'transaction-status', 'isolation-level',
         'provider-owned-finality'),
        'unit:test_cdeadmin_distributed_providers',
    ),
}


def _analytic_http_capabilities(authentication, session_defaults,
                                topology=None):
    evidence = 'unit:test_cdeadmin_analytic_providers'
    topology = tuple(topology or ())
    return {
        'authentication': _complete(authentication, evidence),
        'tls': _complete(
            ('disabled', 'encrypted-without-verification', 'CA-validation',
             'CA-and-hostname-validation'), evidence,
        ),
        'client_certificates': _complete(
            ('certificate-and-key', 'encrypted-private-key'), evidence,
            'unit:test_cdeadmin_credential_bundle',
        ),
        'connection_profiles': _complete(
            ('persistent-provider-profile', 'editable-connection-options',
             'typed-multi-secret-bundle',
             'generation-based-runtime-invalidation'),
            PROFILE_EVIDENCE, ROUTE_EVIDENCE,
        ),
        'multiple_endpoints': _complete(
            ('persistent-prioritized-routes',
             'bounded-pre-session-route-selection'),
            ROUTE_EVIDENCE, evidence,
        ),
        'topology_discovery': (
            _complete(topology, evidence) if topology else _na(
                'The qualified HTTP API exposes no client-side cluster '
                'topology discovery contract.', evidence,
            )
        ),
        'routing_failover': _complete(
            ('health-aware-prioritized-routes',
             'bounded-pre-session-route-failover',
             'zero-common-layer-mutation-replay'),
            ROUTE_EVIDENCE, evidence,
        ),
        'session_defaults': _complete(session_defaults, evidence),
        'timeouts': _complete(
            ('connect-timeout', 'request-read-timeout'), evidence,
        ),
        'compression': _complete(
            ('none', 'HTTP-gzip-request-and-response'), evidence,
        ),
        'consistency_isolation': _na(
            'The qualified HTTP API has request-level outcomes and exposes '
            'no connection-level transaction isolation selection.', evidence,
        ),
        'pooling': _complete(
            ('urllib3-per-route-pool', 'maximum-connections',
             'blocking-acquisition-policy',
             'generation-based-pool-retirement'),
            evidence, 'unit:test_cdeadmin_provider_registry',
        ),
        'reconnection': _complete(
            ('discard-failed-HTTP-connection',
             'explicit-new-request-after-loss',
             'zero-automatic-mutation-retry',
             'bounded-pre-session-route-failover'),
            evidence, ROUTE_EVIDENCE,
        ),
        'state_visibility': _complete(
            ('session-route', 'last-native-request-observation',
             'request-atomicity', 'transaction-finality-opaque'), evidence,
        ),
    }


INFLUXDB_HTTP = _analytic_http_capabilities(
    ('none', 'bearer-token'),
    ('database', 'SQL-or-InfluxQL', 'read-only-policy'),
)
OPENSEARCH_HTTP = _analytic_http_capabilities(
    ('none', 'basic', 'bearer-token', 'API-key', 'AWS-SigV4',
     'mutual-TLS'),
    ('default-index', 'read-only-policy'),
    ('cluster-identity', 'node-catalog', 'cluster-health'),
)
OPENSEARCH_SQL_PPL_HTTP = _analytic_http_capabilities(
    ('none', 'basic', 'bearer-token', 'API-key', 'AWS-SigV4',
     'mutual-TLS'),
    ('data-source', 'SQL-or-PPL', 'fetch-size', 'read-only-policy'),
    ('OpenSearch-cluster-identity', 'SQL-plugin-identity'),
)
CLICKHOUSE_HTTP = _analytic_http_capabilities(
    ('none', 'username-password', 'mutual-TLS'),
    ('database', 'read-only', 'HTTP-session-ID', 'quota-key',
     'initial-role'),
    ('system-clusters', 'replica-catalog', 'server-identity'),
)
CLICKHOUSE_HTTP['consistency_isolation'] = _complete(
    ('read-only-session-policy', 'synchronous-mutation-observation',
     'synchronous-alter-observation'),
    'unit:test_cdeadmin_clickhouse_provider',
)
for _category in CLICKHOUSE_HTTP.values():
    if _category['state'] == 'complete':
        _category['evidence'] = [
            'unit:test_cdeadmin_clickhouse_provider'
            if item == 'unit:test_cdeadmin_analytic_providers' else item
            for item in _category['evidence']
        ]

MILVUS_GRPC = {
    'authentication': _complete(
        ('none', 'username-password', 'API-token'),
        'unit:test_cdeadmin_analytic_providers',
    ),
    'tls': _complete(
        ('disabled', 'server-TLS', 'CA-validation',
         'server-name-validation'),
        'unit:test_cdeadmin_analytic_providers',
    ),
    'client_certificates': _complete(
        ('certificate-and-key',),
        'unit:test_cdeadmin_analytic_providers',
    ),
    'connection_profiles': _complete(
        ('persistent-provider-profile', 'editable-connection-options',
         'typed-secret-bundle', 'generation-based-runtime-invalidation'),
        PROFILE_EVIDENCE, ROUTE_EVIDENCE,
    ),
    'multiple_endpoints': _complete(
        ('persistent-prioritized-routes',
         'bounded-pre-session-route-selection'), ROUTE_EVIDENCE,
    ),
    'topology_discovery': _na(
        'PyMilvus exposes the connected Milvus service rather than its '
        'internal coordinator topology.',
        'unit:test_cdeadmin_analytic_providers',
    ),
    'routing_failover': _complete(
        ('gRPC-name-resolution', 'driver-reconnect-handler',
         'bounded-pre-session-route-failover'),
        'unit:test_cdeadmin_analytic_providers', ROUTE_EVIDENCE,
    ),
    'session_defaults': _complete(
        ('database', 'consistency-level', 'read-only-policy'),
        'unit:test_cdeadmin_analytic_providers',
    ),
    'timeouts': _complete(
        ('connect', 'operation'),
        'unit:test_cdeadmin_analytic_providers',
    ),
    'compression': _na(
        'PyMilvus 2.6.5 exposes no client-selectable gRPC compression '
        'setting.', 'unit:test_cdeadmin_analytic_providers',
    ),
    'consistency_isolation': _complete(
        ('Strong', 'Bounded', 'Session', 'Eventually', 'Customized'),
        'unit:test_cdeadmin_analytic_providers',
    ),
    'pooling': _complete(
        ('gRPC-managed-channel', 'keep-alive'),
        'unit:test_cdeadmin_analytic_providers',
    ),
    'reconnection': _complete(
        ('PyMilvus-reconnect-handler', 'bounded-pre-session-route-failover',
         'zero-common-layer-mutation-replay'),
        'unit:test_cdeadmin_analytic_providers', ROUTE_EVIDENCE,
    ),
    'state_visibility': _complete(
        ('database', 'consistency-level', 'last-operation-observation',
         'transaction-finality-opaque'),
        'unit:test_cdeadmin_analytic_providers',
    ),
}


XTDB_POSTGRESQL_WIRE = copy.deepcopy(POSTGRESQL_WIRE)
XTDB_POSTGRESQL_WIRE['authentication'] = _complete(
    ('trust', 'user-table-password', 'OIDC-client-credentials',
     'OIDC-device-authorization'),
    'unit:test_cdeadmin_xtdb_provider',
)
XTDB_POSTGRESQL_WIRE['session_defaults'] = _complete(
    ('database', 'application-name', 'statement-timeout',
     'read-only-policy', 'transaction-mode', 'transaction-timezone'),
    'unit:test_cdeadmin_xtdb_provider',
)
XTDB_POSTGRESQL_WIRE['consistency_isolation'] = _complete(
    ('inferred-transaction-mode', 'read-only', 'read-write',
     'synchronous-or-asynchronous-commit'),
    'unit:test_cdeadmin_xtdb_provider',
)
XTDB_POSTGRESQL_WIRE['state_visibility'] = _complete(
    ('autocommit', 'native-transaction-observation', 'transaction-basis',
     'configured-transaction-defaults', 'provider-owned-finality'),
    'unit:test_cdeadmin_xtdb_provider',
)
for _category in XTDB_POSTGRESQL_WIRE.values():
    if _category['state'] == 'complete':
        _category['evidence'] = [
            'unit:test_cdeadmin_xtdb_provider'
            if item == 'unit:test_cdeadmin_distributed_providers' else item
            for item in _category['evidence']
        ]


TIKV_CLIENT_GO = {
    'authentication': _na(
        'TiKV client connections use mutually authenticated cluster TLS; '
        'the reference engine exposes no user/password client login.',
        'unit:test_cdeadmin_distributed_providers',
    ),
    'tls': _complete(
        ('disabled', 'private-CA-validation', 'server-name-validation'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'client_certificates': _complete(
        ('certificate-and-unencrypted-private-key',),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'connection_profiles': _complete(
        ('persistent-provider-profile', 'editable-connection-options',
         'generation-based-runtime-invalidation'),
        PROFILE_EVIDENCE, ROUTE_EVIDENCE,
    ),
    'multiple_endpoints': _complete(
        ('PD-endpoint-list', 'persistent-prioritized-routes'),
        'unit:test_cdeadmin_distributed_providers', ROUTE_EVIDENCE,
    ),
    'topology_discovery': _complete(
        ('PD-cluster-discovery', 'store-and-region-catalog'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'routing_failover': _complete(
        ('client-go-region-routing', 'PD-leader-discovery',
         'bounded-pre-session-route-failover'),
        'unit:test_cdeadmin_distributed_providers', ROUTE_EVIDENCE,
    ),
    'session_defaults': _complete(
        ('API-version', 'optimistic-or-pessimistic-transactions'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'timeouts': _complete(
        ('native-operation-deadline', 'helper-process-deadline',
         'PD-metadata-request-deadline'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'compression': _na(
        'The qualified TiKV client-go API exposes no client-selectable '
        'transport compression setting.',
        'unit:test_cdeadmin_distributed_providers',
    ),
    'consistency_isolation': _complete(
        ('snapshot-isolation', 'optimistic-transactions',
         'pessimistic-transactions', 'RawKV-atomic-compare-and-swap'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'pooling': _complete(
        ('client-go-managed-gRPC-connections',),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'reconnection': _complete(
        ('client-go-PD-refresh', 'region-cache-refresh',
         'bounded-pre-session-route-failover',
         'zero-common-layer-mutation-replay'),
        'unit:test_cdeadmin_distributed_providers', ROUTE_EVIDENCE,
    ),
    'state_visibility': _complete(
        ('transaction-mode', 'start-timestamp', 'commit-observation',
         'provider-owned-finality'),
        'unit:test_cdeadmin_distributed_providers',
    ),
}


APACHE_IGNITE_THIN = {
    'authentication': _complete(
        ('none', 'username-password'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'tls': _complete(
        ('disabled', 'encrypted-without-verification',
         'private-CA-validation'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'client_certificates': _complete(
        ('certificate-and-key', 'encrypted-private-key'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'connection_profiles': _complete(
        ('persistent-provider-profile', 'editable-connection-options',
         'typed-secret-bundle', 'generation-based-runtime-invalidation'),
        PROFILE_EVIDENCE, ROUTE_EVIDENCE,
    ),
    'multiple_endpoints': _complete(
        ('thin-client-endpoint-list', 'persistent-prioritized-routes'),
        'unit:test_cdeadmin_distributed_providers', ROUTE_EVIDENCE,
    ),
    'topology_discovery': _complete(
        ('partition-awareness', 'server-node-topology', 'cache-catalog'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'routing_failover': _complete(
        ('partition-aware-node-routing', 'multi-endpoint-connect',
         'bounded-pre-session-route-failover'),
        'unit:test_cdeadmin_distributed_providers', ROUTE_EVIDENCE,
    ),
    'session_defaults': _complete(
        ('compact-binary-footer', 'partition-awareness',
         'transaction-concurrency', 'transaction-isolation'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'timeouts': _complete(
        ('socket', 'handshake', 'REST-request', 'transaction'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'compression': _na(
        'The Ignite 2.17 thin-client protocol exposes no client-selectable '
        'transport compression setting.',
        'unit:test_cdeadmin_distributed_providers',
    ),
    'consistency_isolation': _complete(
        ('optimistic-or-pessimistic', 'read-committed',
         'repeatable-read', 'serializable'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'pooling': _complete(
        ('driver-managed-per-node-connections',),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'reconnection': _complete(
        ('driver-node-reconnect', 'partition-map-refresh',
         'bounded-pre-session-route-failover',
         'zero-common-layer-mutation-replay'),
        'unit:test_cdeadmin_distributed_providers', ROUTE_EVIDENCE,
    ),
    'state_visibility': _complete(
        ('configured-transaction-defaults', 'native-session-state',
         'provider-owned-finality'),
        'unit:test_cdeadmin_distributed_providers',
    ),
}


MONGODB_PYMONGO = {
    'authentication': _complete(
        ('none', 'driver-negotiated-SCRAM', 'SCRAM-SHA-1',
         'SCRAM-SHA-256', 'MONGODB-X509', 'GSSAPI', 'PLAIN',
         'MONGODB-AWS', 'MONGODB-OIDC', 'Stable-API-v1'),
        'unit:test_cdeadmin_mongodb_provider',
    ),
    'tls': _complete(
        ('system-or-private-CA', 'hostname-validation', 'CRL', 'OCSP',
         'explicit-insecure-development-modes'),
        'unit:test_cdeadmin_mongodb_provider',
    ),
    'client_certificates': _complete(
        ('combined-certificate-key', 'encrypted-private-key'),
        'unit:test_cdeadmin_mongodb_provider',
        'unit:test_cdeadmin_credential_bundle',
    ),
    'connection_profiles': _complete(
        ('persistent-provider-profile', 'editable-connection-options',
         'typed-multi-secret-bundle',
         'generation-based-runtime-invalidation'),
        PROFILE_EVIDENCE, ROUTE_EVIDENCE,
    ),
    'multiple_endpoints': _complete(
        ('seed-list', 'DNS-SRV', 'persistent-prioritized-routes'),
        'unit:test_cdeadmin_mongodb_provider', ROUTE_EVIDENCE,
    ),
    'topology_discovery': _complete(
        ('replica-set', 'mongos', 'load-balanced-service',
         'streaming-or-polling-monitor'),
        'unit:test_cdeadmin_mongodb_provider',
    ),
    'routing_failover': _complete(
        ('read-preference-routing', 'latency-window', 'tag-sets',
         'maximum-staleness', 'adaptive-overload-retargeting',
         'bounded-pre-session-route-failover'),
        'unit:test_cdeadmin_mongodb_provider', ROUTE_EVIDENCE,
    ),
    'session_defaults': _complete(
        ('database', 'application-name', 'causal-consistency',
         'snapshot-session', 'UUID-representation', 'timezone-awareness',
         'Unicode-error-policy', 'Stable-API'),
        'unit:test_cdeadmin_mongodb_provider',
    ),
    'timeouts': _complete(
        ('connect', 'server-selection', 'socket', 'operation',
         'pool-wait', 'transaction-commit'),
        'unit:test_cdeadmin_mongodb_provider',
    ),
    'compression': _complete(
        ('none', 'Snappy', 'zlib-with-level', 'zstd'),
        'unit:test_cdeadmin_mongodb_provider',
    ),
    'consistency_isolation': _complete(
        ('five-read-preferences', 'read-concern', 'write-concern',
         'journal-or-fsync-acknowledgement', 'transaction-concerns',
         'causal-consistency', 'snapshot-session'),
        'unit:test_cdeadmin_mongodb_provider',
    ),
    'pooling': _complete(
        ('minimum-and-maximum-size', 'maximum-connecting',
         'maximum-idle-time', 'bounded-wait-queue',
         'generation-based-pool-retirement'),
        'unit:test_cdeadmin_mongodb_provider',
        'unit:test_cdeadmin_provider_registry',
    ),
    'reconnection': _complete(
        ('topology-heartbeats', 'retryable-reads', 'retryable-writes',
         'bounded-adaptive-read-retries',
         'zero-common-layer-mutation-replay'),
        'unit:test_cdeadmin_mongodb_provider',
    ),
    'state_visibility': _complete(
        ('session-ID', 'transaction-state', 'runtime-topology',
         'read-write-concerns', 'last-operation-observation',
         'provider-owned-finality'),
        'unit:test_cdeadmin_mongodb_provider',
    ),
}


FOUNDATIONDB_NATIVE = {
    'authentication': _complete(
        ('cluster-file-coordinator-authority', 'mutual-TLS-peer-identity'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'tls': _complete(
        ('private-CA', 'plugin-peer-verification-constraints'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'client_certificates': _complete(
        ('certificate-and-key', 'encrypted-private-key'),
        'unit:test_cdeadmin_distributed_providers',
        'unit:test_cdeadmin_credential_bundle',
    ),
    'connection_profiles': _complete(
        ('persistent-provider-profile', 'editable-connection-options',
         'typed-secret-reference', 'fail-closed-network-fingerprint'),
        PROFILE_EVIDENCE, ROUTE_EVIDENCE,
    ),
    'multiple_endpoints': _complete(
        ('cluster-file-coordinator-set', 'persistent-prioritized-routes'),
        'unit:test_cdeadmin_distributed_providers', ROUTE_EVIDENCE,
    ),
    'topology_discovery': _complete(
        ('coordinator-discovery', 'process-catalog', 'cluster-status'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'routing_failover': _complete(
        ('native-coordinator-failover', 'multi-version-client',
         'bounded-pre-session-route-failover'),
        'unit:test_cdeadmin_distributed_providers', ROUTE_EVIDENCE,
    ),
    'session_defaults': _complete(
        ('API-version-730', 'external-client-library-selection',
         'client-threads-per-version', 'transaction-policy'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'timeouts': _complete(
        ('transaction-timeout', 'retry-limit', 'maximum-retry-delay',
         'control-command-deadline'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'compression': _na(
        'FoundationDB API 730 exposes no client-selectable transport '
        'compression setting.', 'unit:test_cdeadmin_distributed_providers',
    ),
    'consistency_isolation': _complete(
        ('strict-serializability', 'native-conflict-ranges',
         'native-idempotent-retry-policy'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'pooling': _complete(
        ('process-global-network-thread-pool', 'multi-version-client'),
        'unit:test_cdeadmin_distributed_providers',
    ),
    'reconnection': _complete(
        ('native-coordinator-reconnection', 'native-transaction-retry',
         'bounded-pre-session-route-failover',
         'zero-common-layer-mutation-replay'),
        'unit:test_cdeadmin_distributed_providers', ROUTE_EVIDENCE,
    ),
    'state_visibility': _complete(
        ('cluster-status', 'strict-serializable-model',
         'configured-transaction-policy', 'provider-owned-finality'),
        'unit:test_cdeadmin_distributed_providers',
    ),
}


CONNECTION_CAPABILITY_SETS = {
    'cql-native-v1': CQL,
    'redis-py-v1': REDIS,
    'neo4j-bolt-v1': NEO4J,
    'mysql-connector-python-v1': MYSQL,
    'mariadb-connector-python-v1': MARIADB,
    'firebird-driver-v1': FIREBIRD,
    'postgresql-wire-v1': POSTGRESQL_WIRE,
    'influxdb-http-v1': INFLUXDB_HTTP,
    'opensearch-http-v1': OPENSEARCH_HTTP,
    'opensearch-sql-ppl-http-v1': OPENSEARCH_SQL_PPL_HTTP,
    'clickhouse-http-v1': CLICKHOUSE_HTTP,
    'milvus-grpc-v1': MILVUS_GRPC,
    'xtdb-postgresql-wire-v1': XTDB_POSTGRESQL_WIRE,
    'tikv-client-go-v1': TIKV_CLIENT_GO,
    'apache-ignite-thin-v1': APACHE_IGNITE_THIN,
    'mongodb-pymongo-v1': MONGODB_PYMONGO,
    'foundationdb-native-v1': FOUNDATIONDB_NATIVE,
}


def expand_capability_set(registration):
    """Resolve one explicitly selected reusable connection declaration."""
    name = registration.get('connection_capability_set')
    inline = registration.get('connection_capabilities')
    if name is None:
        return copy.deepcopy(inline)
    if inline is not None:
        raise KeyError('connection capability set conflicts with inline data')
    if not isinstance(name, str) or name not in CONNECTION_CAPABILITY_SETS:
        raise KeyError(str(name))
    return {
        'contract_version': '1.0',
        'categories': copy.deepcopy(CONNECTION_CAPABILITY_SETS[name]),
    }


__all__ = ('CONNECTION_CAPABILITY_SETS', 'expand_capability_set')
