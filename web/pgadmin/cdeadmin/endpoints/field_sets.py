##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Shared protocol field sets explicitly selected by provider manifests."""

from __future__ import annotations

import copy


def _field(field_id, label, control='text', group='Advanced connection',
           **options):
    return {
        'field_id': field_id, 'route_key': field_id, 'label': label,
        'control': control, 'group': group, **options,
    }


def _select(field_id, label, values, group, default=None, **options):
    result = _field(
        field_id, label, 'select', group,
        options=[{'value': value, 'label': caption}
                 for value, caption in values],
        **options,
    )
    if default is not None:
        result['default'] = default
    return result


PG = 'PostgreSQL-wire connection'
PG_TLS = 'PostgreSQL-wire TLS'
PG_AUTH = 'PostgreSQL-wire authentication'
PG_SESSION = 'PostgreSQL-wire session'
PG_NETWORK = 'PostgreSQL-wire network'
PG_POOL = 'PostgreSQL-wire pool'

POSTGRESQL_WIRE_FIELDS = (
    _field('hostaddr', 'Host address', group=PG_NETWORK),
    _field('connect_timeout', 'Connect timeout (seconds)', 'number',
           PG_NETWORK, default=10, minimum=1, maximum=600),
    _select('sslmode', 'TLS mode', (
        ('disable', 'Disable'), ('allow', 'Allow'), ('prefer', 'Prefer'),
        ('require', 'Require'), ('verify-ca', 'Verify CA'),
        ('verify-full', 'Verify CA and host'),
    ), PG_TLS, 'prefer'),
    _field('sslrootcert', 'Root certificate', 'file', PG_TLS),
    _field('sslcert', 'Client certificate', 'file', PG_TLS),
    _field('sslkey', 'Client certificate key', 'file', PG_TLS),
    _field('sslcrl', 'Certificate revocation list', 'file', PG_TLS),
    _field('sslcrldir', 'Certificate revocation directory', 'file', PG_TLS),
    _field('sslsni', 'Send TLS SNI', 'boolean', PG_TLS, default=True),
    _field('requirepeer', 'Required peer name', group=PG_TLS),
    _select('ssl_min_protocol_version', 'Minimum TLS version', (
        ('TLSv1', 'TLS 1.0'), ('TLSv1.1', 'TLS 1.1'),
        ('TLSv1.2', 'TLS 1.2'), ('TLSv1.3', 'TLS 1.3'),
    ), PG_TLS),
    _select('ssl_max_protocol_version', 'Maximum TLS version', (
        ('TLSv1', 'TLS 1.0'), ('TLSv1.1', 'TLS 1.1'),
        ('TLSv1.2', 'TLS 1.2'), ('TLSv1.3', 'TLS 1.3'),
    ), PG_TLS),
    _select('sslnegotiation', 'TLS negotiation', (
        ('postgres', 'PostgreSQL negotiation'), ('direct', 'Direct TLS'),
    ), PG_TLS, 'postgres'),
    _select('channel_binding', 'Channel binding', (
        ('disable', 'Disable'), ('prefer', 'Prefer'),
        ('require', 'Require'),
    ), PG_AUTH, 'prefer'),
    _select('gssencmode', 'GSS encryption', (
        ('disable', 'Disable'), ('prefer', 'Prefer'),
        ('require', 'Require'),
    ), PG_AUTH, 'prefer'),
    _field('krbsrvname', 'Kerberos service name', group=PG_AUTH,
           default='postgres'),
    _field('gsslib', 'GSS library', group=PG_AUTH),
    _field('gssdelegation', 'Delegate GSS credentials', 'boolean', PG_AUTH,
           default=False),
    _field('require_auth', 'Required authentication methods', group=PG_AUTH),
    _field('oauth_issuer', 'OAuth issuer', group=PG_AUTH),
    _field('oauth_client_id', 'OAuth client ID', group=PG_AUTH),
    _field('oauth_scope', 'OAuth scope', group=PG_AUTH),
    _select('target_session_attrs', 'Target session attributes', (
        ('any', 'Any'), ('read-write', 'Read/write'),
        ('read-only', 'Read only'), ('primary', 'Primary'),
        ('standby', 'Standby'), ('prefer-standby', 'Prefer standby'),
    ), PG_SESSION, 'any'),
    _select('load_balance_hosts', 'Host load balancing', (
        ('disable', 'Disable'), ('random', 'Random'),
    ), PG_NETWORK, 'disable'),
    _field('application_name', 'Application name', group=PG_SESSION,
           default='CDEadmin'),
    _field('fallback_application_name', 'Fallback application name',
           group=PG_SESSION, default='CDEadmin'),
    _field('client_encoding', 'Client encoding', group=PG_SESSION),
    _field('options', 'Server command-line options', 'multiline', PG_SESSION),
    _field('autocommit', 'Autocommit', 'boolean', PG_SESSION,
           default=False),
    _select('transaction_isolation', 'Transaction isolation', (
        ('server', 'Server default'),
        ('READ_UNCOMMITTED', 'Read uncommitted'),
        ('READ_COMMITTED', 'Read committed'),
        ('REPEATABLE_READ', 'Repeatable read'),
        ('SERIALIZABLE', 'Serializable'),
    ), PG_SESSION, 'server'),
    _select('transaction_read_only', 'Transaction read mode', (
        ('server', 'Server default'), ('read-write', 'Read/write'),
        ('read-only', 'Read only'),
    ), PG_SESSION, 'server'),
    _select('transaction_deferrable', 'Transaction deferrability', (
        ('server', 'Server default'), ('deferrable', 'Deferrable'),
        ('not-deferrable', 'Not deferrable'),
    ), PG_SESSION, 'server'),
    _field('keepalives', 'Enable TCP keepalives', 'boolean', PG_NETWORK,
           default=True),
    _field('keepalives_idle', 'TCP keepalive idle (seconds)', 'number',
           PG_NETWORK, minimum=0, maximum=86400),
    _field('keepalives_interval', 'TCP keepalive interval (seconds)',
           'number', PG_NETWORK, minimum=0, maximum=86400),
    _field('keepalives_count', 'TCP keepalive count', 'number', PG_NETWORK,
           minimum=0, maximum=1000),
    _field('tcp_user_timeout', 'TCP user timeout (milliseconds)', 'number',
           PG_NETWORK, minimum=0, maximum=86400000),
    _field('pool_enabled', 'Enable connection pooling', 'boolean', PG_POOL,
           default=False),
    _field('pool_min_size', 'Minimum pool size', 'number', PG_POOL,
           default=1, minimum=0, maximum=1000,
           visible_when={'field_id': 'pool_enabled', 'equals': True}),
    _field('pool_max_size', 'Maximum pool size', 'number', PG_POOL,
           default=10, minimum=1, maximum=1000,
           visible_when={'field_id': 'pool_enabled', 'equals': True}),
    _field('pool_acquisition_timeout', 'Pool acquisition timeout (seconds)',
           'number', PG_POOL, default=30, minimum=1, maximum=3600,
           visible_when={'field_id': 'pool_enabled', 'equals': True}),
    _field('pool_max_waiting', 'Maximum queued clients (0 is unlimited)',
           'number', PG_POOL, default=0, minimum=0, maximum=100000,
           visible_when={'field_id': 'pool_enabled', 'equals': True}),
    _field('pool_max_lifetime', 'Maximum connection lifetime (seconds)',
           'number', PG_POOL, default=3600, minimum=1, maximum=86400,
           visible_when={'field_id': 'pool_enabled', 'equals': True}),
    _field('pool_max_idle', 'Maximum idle time (seconds)', 'number', PG_POOL,
           default=600, minimum=1, maximum=86400,
           visible_when={'field_id': 'pool_enabled', 'equals': True}),
    _field('pool_reconnect_timeout', 'Background reconnect timeout (seconds)',
           'number', PG_POOL, default=300, minimum=0, maximum=86400,
           visible_when={'field_id': 'pool_enabled', 'equals': True}),
    _field('pool_workers', 'Pool background workers', 'number', PG_POOL,
           default=3, minimum=1, maximum=128,
           visible_when={'field_id': 'pool_enabled', 'equals': True}),
    _field('pool_check_connection', 'Check connections on checkout',
           'boolean', PG_POOL, default=True,
           visible_when={'field_id': 'pool_enabled', 'equals': True}),
)

MYSQL = 'MySQL-wire connection'
MYSQL_TLS = 'MySQL-wire TLS'
MYSQL_AUTH = 'MySQL-wire authentication'
MYSQL_SESSION = 'MySQL-wire session'
MYSQL_POOL = 'MySQL-wire pool'

MYSQL_WIRE_FIELDS = (
    _field('unix_socket', 'Unix socket', 'file', MYSQL),
    _field('connection_timeout', 'Connect timeout (seconds)', 'number',
           MYSQL, default=10, minimum=1, maximum=600),
    _field('read_timeout', 'Read timeout (seconds)', 'number', MYSQL,
           minimum=1, maximum=86400),
    _field('write_timeout', 'Write timeout (seconds)', 'number', MYSQL,
           minimum=1, maximum=86400),
    _field('failover', 'Failover endpoints (JSON array)', 'json', MYSQL),
    _field('dns_srv', 'Use DNS SRV discovery', 'boolean', MYSQL,
           default=False, conflicts_with=['failover']),
    _field('force_ipv6', 'Force IPv6', 'boolean', MYSQL, default=False),
    _select('auth_plugin', 'Authentication plugin', (
        ('auto', 'Server-selected'),
        ('mysql_native_password', 'mysql_native_password'),
        ('caching_sha2_password', 'caching_sha2_password'),
        ('sha256_password', 'sha256_password'),
        ('authentication_kerberos_client', 'Kerberos'),
        ('authentication_oci_client', 'OCI IAM'),
        ('authentication_webauthn_client', 'WebAuthn'),
        ('authentication_openid_connect_client', 'OpenID Connect'),
    ), MYSQL_AUTH, 'auto'),
    _field('krb_service_principal', 'Kerberos service principal',
           group=MYSQL_AUTH),
    _select('kerberos_auth_mode', 'Kerberos mode', (
        ('SSPI', 'SSPI'), ('GSSAPI', 'GSSAPI'),
    ), MYSQL_AUTH),
    _field('oci_config_file', 'OCI configuration file', 'file', MYSQL_AUTH),
    _field('oci_config_profile', 'OCI configuration profile',
           group=MYSQL_AUTH),
    _field('openid_token_file', 'OpenID Connect token file', 'file',
           MYSQL_AUTH),
    _field('ssl_disabled', 'Disable TLS', 'boolean', MYSQL_TLS,
           default=False),
    _field('ssl_ca', 'Certificate authority file', 'file', MYSQL_TLS),
    _field('ssl_cert', 'Client certificate', 'file', MYSQL_TLS),
    _field('ssl_key', 'Client key', 'file', MYSQL_TLS),
    _field('ssl_verify_cert', 'Verify server certificate', 'boolean',
           MYSQL_TLS, default=True),
    _field('ssl_verify_identity', 'Verify server identity', 'boolean',
           MYSQL_TLS, default=True),
    _field('ssl_cipher', 'TLS cipher list', group=MYSQL_TLS),
    _field('tls_versions', 'TLS versions (JSON array)', 'json', MYSQL_TLS),
    _field('tls_ciphersuites', 'TLS cipher suites (JSON array)', 'json',
           MYSQL_TLS),
    _field('compress', 'Protocol compression', 'boolean', MYSQL,
           default=False),
    _field('charset', 'Character set', group=MYSQL_SESSION,
           default='utf8mb4'),
    _field('collation', 'Collation', group=MYSQL_SESSION),
    _field('autocommit', 'Autocommit', 'boolean', MYSQL_SESSION,
           default=False),
    _field('time_zone', 'Session time zone', group=MYSQL_SESSION),
    _field('sql_mode', 'SQL mode', group=MYSQL_SESSION),
    _field('init_command', 'Initial session command', 'multiline',
           MYSQL_SESSION, conflicts_with=['transaction_isolation']),
    _select('transaction_isolation', 'Transaction isolation', (
        ('READ UNCOMMITTED', 'Read uncommitted'),
        ('READ COMMITTED', 'Read committed'),
        ('REPEATABLE READ', 'Repeatable read'),
        ('SERIALIZABLE', 'Serializable'),
    ), MYSQL_SESSION),
    _field('conn_attrs', 'Connection attributes (JSON object)', 'json',
           MYSQL_SESSION),
    _field('pool_size', 'Connection pool size', 'number', MYSQL_POOL,
           minimum=1, maximum=32),
    _field('pool_reset_session', 'Reset sessions on pool return', 'boolean',
           MYSQL_POOL, default=True),
)

MARIADB = 'MariaDB connection'
MARIADB_TLS = 'MariaDB TLS'
MARIADB_POOL = 'MariaDB pool'
MARIADB_FIELDS = (
    _field('unix_socket', 'Unix socket', 'file', MARIADB),
    _field('connection_timeout', 'Connect timeout (seconds)', 'number',
           MARIADB, default=10, minimum=1, maximum=600),
    _field('read_timeout', 'Read timeout (seconds)', 'number', MARIADB,
           minimum=1, maximum=86400),
    _field('write_timeout', 'Write timeout (seconds)', 'number', MARIADB,
           minimum=1, maximum=86400),
    _field('local_infile', 'Permit LOAD DATA LOCAL INFILE', 'boolean',
           MARIADB, default=False),
    _field('compress', 'Protocol compression', 'boolean', MARIADB,
           default=False),
    _field('init_command', 'Initial/reconnect session command', 'multiline',
           MARIADB, conflicts_with=['transaction_isolation']),
    _select('transaction_isolation', 'Transaction isolation', (
        ('READ UNCOMMITTED', 'Read uncommitted'),
        ('READ COMMITTED', 'Read committed'),
        ('REPEATABLE READ', 'Repeatable read'),
        ('SERIALIZABLE', 'Serializable'),
    ), MARIADB),
    _field('default_file', 'Client option file', 'file', MARIADB),
    _field('default_group', 'Client option group', group=MARIADB),
    _field('plugin_dir', 'Authentication plugin directory', 'file', MARIADB),
    _field('reconnect', 'Driver automatic reconnect', 'boolean', MARIADB,
           default=False),
    _field('ssl', 'Require TLS', 'boolean', MARIADB_TLS, default=False),
    _field('ssl_ca', 'Certificate authority file', 'file', MARIADB_TLS),
    _field('ssl_capath', 'Certificate authority directory', 'file',
           MARIADB_TLS),
    _field('ssl_cert', 'Client certificate', 'file', MARIADB_TLS),
    _field('ssl_key', 'Client key', 'file', MARIADB_TLS),
    _field('ssl_crlpath', 'Certificate revocation list', 'file', MARIADB_TLS),
    _field('ssl_cipher', 'TLS cipher list', group=MARIADB_TLS),
    _field('ssl_verify_cert', 'Verify server certificate', 'boolean',
           MARIADB_TLS, default=True),
    _field('tls_version', 'TLS versions (comma separated)', group=MARIADB_TLS),
    _field('autocommit', 'Autocommit', 'boolean', MARIADB, default=False),
    _field('pool_size', 'Connection pool size', 'number', MARIADB_POOL,
           minimum=1, maximum=64),
    _field('pool_reset_connection', 'Reset sessions on pool return',
           'boolean', MARIADB_POOL, default=True),
    _field('pool_validation_interval', 'Pool validation interval (ms)',
           'number', MARIADB_POOL, default=500, minimum=0,
           maximum=3600000),
)

POSTGRESQL_WIRE_SECRETS = (
    {'field_id': 'password', 'secret_kind': 'database_password',
     'label': 'Password', 'group': PG_AUTH, 'primary': True},
    {'field_id': 'tls_key_password',
     'secret_kind': 'tls_private_key_password',
     'label': 'Client private-key password', 'group': PG_TLS},
    {'field_id': 'oauth_client_secret',
     'secret_kind': 'oauth_client_secret',
     'label': 'OAuth client secret', 'group': PG_AUTH},
)

MYSQL_WIRE_SECRETS = (
    {'field_id': 'password', 'secret_kind': 'database_password',
     'label': 'Password / first factor', 'group': MYSQL_AUTH,
     'primary': True},
    {'field_id': 'password_2', 'secret_kind': 'database_password_2',
     'label': 'Second authentication factor', 'group': MYSQL_AUTH},
    {'field_id': 'password_3', 'secret_kind': 'database_password_3',
     'label': 'Third authentication factor', 'group': MYSQL_AUTH},
)

MARIADB_SECRETS = (
    {'field_id': 'password', 'secret_kind': 'database_password',
     'label': 'Password', 'group': MARIADB, 'primary': True},
)

CQL = 'CQL native protocol'
CQL_AUTH = 'CQL authentication'
CQL_TLS = 'CQL TLS'
CQL_TOPOLOGY = 'CQL topology and routing'
CQL_SESSION = 'CQL session and retry policy'
CQL_FIELDS = (
    _select('auth_mode', 'Authentication mode', (
        ('none', 'No authentication'),
        ('password', 'Username and password'),
        ('mutual-tls', 'Mutual TLS certificate'),
        ('password-mutual-tls', 'Password and mutual TLS'),
    ), CQL_AUTH, 'none'),
    _field('contact_points', 'Additional contact points (comma separated)',
           group=CQL_TOPOLOGY),
    _field('local_dc', 'Local datacenter', group=CQL_TOPOLOGY),
    _select('load_balancing_policy', 'Load-balancing policy', (
        ('dc-aware', 'Datacenter-aware round robin'),
        ('round-robin', 'Round robin'),
        ('token-aware-dc', 'Token-aware, datacenter-aware'),
        ('token-aware-round-robin', 'Token-aware round robin'),
    ), CQL_TOPOLOGY, 'token-aware-dc'),
    _field('used_hosts_per_remote_dc', 'Remote hosts per datacenter',
           'number', CQL_TOPOLOGY, default=0, minimum=0, maximum=1024),
    _field('allow_remote_dcs_for_local_cl',
           'Use remote datacenters for local consistency', 'boolean',
           CQL_TOPOLOGY, default=False),
    _field('keyspace', 'Initial keyspace', group=CQL_SESSION),
    _select('tls_mode', 'TLS mode', (
        ('disabled', 'Disabled'),
        ('system-ca', 'Verify with system or supplied CA'),
        ('self-signed', 'Encrypted without CA verification'),
    ), CQL_TLS, 'disabled'),
    _field('tls_ca_file', 'Certificate authority file', 'file', CQL_TLS,
           visible_when={'field_id': 'tls_mode', 'in': [
               'system-ca', 'self-signed',
           ]}),
    _field('tls_certificate_file', 'Client certificate', 'file', CQL_TLS,
           visible_when={'field_id': 'auth_mode', 'in': [
               'mutual-tls', 'password-mutual-tls',
           ]}, requires_fields=['tls_key_file']),
    _field('tls_key_file', 'Client private key', 'file', CQL_TLS,
           visible_when={'field_id': 'auth_mode', 'in': [
               'mutual-tls', 'password-mutual-tls',
           ]}, requires_fields=['tls_certificate_file']),
    _field('tls_check_hostname', 'Verify TLS host name', 'boolean', CQL_TLS,
           default=True, visible_when={'field_id': 'tls_mode',
                                       'equals': 'system-ca'}),
    _select('tls_min_version', 'Minimum TLS version', (
        ('TLSv1_2', 'TLS 1.2'), ('TLSv1_3', 'TLS 1.3'),
    ), CQL_TLS, 'TLSv1_2', visible_when={'field_id': 'tls_mode', 'in': [
        'system-ca', 'self-signed',
    ]}),
    _field('tls_ciphers', 'TLS cipher list', group=CQL_TLS,
           visible_when={'field_id': 'tls_mode', 'in': [
               'system-ca', 'self-signed',
           ]}),
    _select('compression', 'Native protocol compression', (
        ('none', 'None'), ('lz4', 'LZ4'), ('snappy', 'Snappy'),
    ), CQL_SESSION, 'none'),
    _select('consistency', 'Default consistency', tuple(
        (value, value) for value in (
            'ANY', 'ONE', 'TWO', 'THREE', 'QUORUM', 'ALL',
            'LOCAL_QUORUM', 'EACH_QUORUM', 'LOCAL_ONE',
        )
    ), CQL_SESSION, 'LOCAL_ONE'),
    _select('serial_consistency', 'Serial consistency', (
        ('SERIAL', 'SERIAL'), ('LOCAL_SERIAL', 'LOCAL_SERIAL'),
    ), CQL_SESSION, 'LOCAL_SERIAL'),
    _field('request_timeout', 'Request timeout (seconds)', 'number',
           CQL_SESSION, default=30, minimum=1, maximum=86400),
    _field('connect_timeout', 'Connect timeout (seconds)', 'number',
           CQL_SESSION, default=10, minimum=1, maximum=600),
    _field('control_connection_timeout',
           'Control connection timeout (seconds)', 'number', CQL_SESSION,
           default=10, minimum=1, maximum=600),
    _field('heartbeat_interval', 'Idle heartbeat interval (seconds)',
           'number', CQL_SESSION, default=30, minimum=0, maximum=3600),
    _field('heartbeat_timeout', 'Idle heartbeat timeout (seconds)',
           'number', CQL_SESSION, default=30, minimum=1, maximum=3600),
    _field('schema_agreement_timeout',
           'Schema agreement timeout (seconds)', 'number', CQL_SESSION,
           default=10, minimum=0, maximum=3600),
    _field('reconnect_base_delay', 'Reconnect base delay (seconds)',
           'number', CQL_SESSION, default=1, minimum=1, maximum=300),
    _field('reconnect_max_delay', 'Reconnect maximum delay (seconds)',
           'number', CQL_SESSION, default=30, minimum=1, maximum=3600),
    _field('reconnect_max_attempts', 'Reconnect maximum attempts', 'number',
           CQL_SESSION, default=64, minimum=1, maximum=100000),
    _field('executor_threads', 'Driver executor threads', 'number',
           CQL_SESSION, default=2, minimum=1, maximum=256),
    _field('application_name', 'Application name', group=CQL_SESSION,
           default='CDEadmin'),
)
CQL_SECRETS = (
    {'field_id': 'password', 'secret_kind': 'database_password',
     'label': 'Password', 'group': CQL_AUTH, 'primary': True,
     'required': True, 'visible_when': {'field_id': 'auth_mode', 'in': [
         'password', 'password-mutual-tls',
     ]}},
    {'field_id': 'tls_key_password',
     'secret_kind': 'tls_private_key_password',
     'label': 'Client private-key password', 'group': CQL_TLS,
     'visible_when': {'field_id': 'auth_mode', 'in': [
         'mutual-tls', 'password-mutual-tls',
     ]}},
)

DUCKDB = 'DuckDB embedded connection'
DUCKDB_FIELDS = (
    _field('read_only', 'Open database read-only', 'boolean', DUCKDB,
           default=False),
    _field('config', 'Connection configuration (JSON object)', 'json',
           DUCKDB, default={}),
    _field('attached_databases', 'Attached databases', 'json', DUCKDB,
           default=[]),
)

SQLITE = 'SQLite embedded connection'
SQLITE_FIELDS = (
    _field('attached_databases', 'Attached databases', 'json', SQLITE,
           default=[]),
    _field('timeout', 'Busy timeout (seconds)', 'number', SQLITE,
           default=5, minimum=0, maximum=86400, integer=False),
    _select('uri_mode', 'File open mode', (
        ('default', 'Default'), ('ro', 'Read only'),
        ('rw', 'Read/write existing'), ('rwc', 'Read/write/create'),
    ), SQLITE, 'default'),
    _select('uri_cache', 'Shared-cache mode', (
        ('default', 'Default'), ('shared', 'Shared'),
        ('private', 'Private'),
    ), SQLITE, 'default'),
    _field('uri_immutable', 'Treat database file as immutable', 'boolean',
           SQLITE, default=False),
    _field('uri_nolock', 'Disable VFS locking', 'boolean', SQLITE,
           default=False),
    _field('uri_vfs', 'VFS implementation name', group=SQLITE),
    _select('detect_types', 'Type detection', (
        ('none', 'Disabled'), ('decltypes', 'Declared types'),
        ('colnames', 'Column names'), ('both', 'Declared and column names'),
    ), SQLITE, 'none'),
    _select('isolation_level', 'Implicit transaction mode', (
        ('legacy', 'Driver legacy default'), ('deferred', 'DEFERRED'),
        ('immediate', 'IMMEDIATE'), ('exclusive', 'EXCLUSIVE'),
        ('autocommit', 'No implicit transaction'),
    ), SQLITE, 'legacy'),
    _field('cached_statements', 'Prepared-statement cache size', 'number',
           SQLITE, default=128, minimum=0, maximum=100000),
)

HTTP = 'HTTP connection'
HTTP_AUTH = 'HTTP authentication'
HTTP_TLS = 'HTTP TLS'
HTTP_POOL = 'HTTP connection pool'
ANALYTIC_HTTP_FIELDS = (
    _select('http_compression', 'HTTP compression', (
        ('none', 'None'), ('gzip', 'Gzip request and response'),
    ), HTTP, 'none'),
    _field('pool_max_size', 'Maximum pooled connections', 'number',
           HTTP_POOL, default=10, minimum=1, maximum=1000),
    _field('pool_block', 'Wait when the connection pool is full', 'boolean',
           HTTP_POOL, default=True),
    _field('aws_access_key_id', 'AWS access key ID', group=HTTP_AUTH,
           visible_when={'field_id': 'auth_kind', 'equals': 'aws-sigv4'}),
    _field('aws_region', 'AWS region', group=HTTP_AUTH,
           visible_when={'field_id': 'auth_kind', 'equals': 'aws-sigv4'}),
    _field('aws_service', 'AWS signing service', group=HTTP_AUTH,
           default='es', visible_when={
               'field_id': 'auth_kind', 'equals': 'aws-sigv4',
           }),
    _field('api_key_header', 'API key header', group=HTTP_AUTH,
           default='Authorization', visible_when={
               'field_id': 'auth_kind', 'equals': 'api-key',
           }),
)
ANALYTIC_HTTP_SECRETS = (
    {'field_id': 'password', 'secret_kind': 'database_password',
     'label': 'Password', 'group': HTTP_AUTH, 'primary': True,
     'visible_when': {'field_id': 'auth_kind', 'equals': 'basic'}},
    {'field_id': 'bearer_token', 'secret_kind': 'api_token',
     'label': 'Bearer token', 'group': HTTP_AUTH, 'primary': True,
     'visible_when': {'field_id': 'auth_kind', 'equals': 'bearer'}},
    {'field_id': 'api_key', 'secret_kind': 'api_key',
     'label': 'API key', 'group': HTTP_AUTH, 'primary': True,
     'visible_when': {'field_id': 'auth_kind', 'equals': 'api-key'}},
    {'field_id': 'aws_secret_access_key',
     'secret_kind': 'cloud_secret_access_key',
     'label': 'AWS secret access key', 'group': HTTP_AUTH, 'primary': True,
     'visible_when': {'field_id': 'auth_kind', 'equals': 'aws-sigv4'}},
    {'field_id': 'aws_session_token', 'secret_kind': 'cloud_session_token',
     'label': 'AWS session token', 'group': HTTP_AUTH,
     'visible_when': {'field_id': 'auth_kind', 'equals': 'aws-sigv4'}},
    {'field_id': 'tls_key_password',
     'secret_kind': 'tls_private_key_password',
     'label': 'Client private-key password', 'group': HTTP_TLS,
     'visible_when': {'field_id': 'tls_mode', 'in': [
         'require', 'verify-ca', 'verify-full',
     ]}},
)

CONNECTION_FIELD_SETS = {
    'postgresql-wire-v1': POSTGRESQL_WIRE_FIELDS,
    'mysql-connector-python-v1': MYSQL_WIRE_FIELDS,
    'mariadb-connector-python-v1': MARIADB_FIELDS,
    'cql-native-v1': CQL_FIELDS,
    'duckdb-embedded-v1': DUCKDB_FIELDS,
    'sqlite-embedded-v1': SQLITE_FIELDS,
    'analytic-http-v1': ANALYTIC_HTTP_FIELDS,
}
SECRET_FIELD_SETS = {
    'postgresql-wire-v1': POSTGRESQL_WIRE_SECRETS,
    'mysql-connector-python-v1': MYSQL_WIRE_SECRETS,
    'mariadb-connector-python-v1': MARIADB_SECRETS,
    'cql-native-v1': CQL_SECRETS,
    'analytic-http-v1': ANALYTIC_HTTP_SECRETS,
}


def expand_field_sets(registration):
    """Expand only explicitly selected, versioned shared field sets."""
    connection = []
    secrets = []
    for name in registration.get('connection_field_sets', []):
        if name not in CONNECTION_FIELD_SETS:
            raise KeyError(name)
        connection.extend(copy.deepcopy(CONNECTION_FIELD_SETS[name]))
    for name in registration.get('secret_field_sets', []):
        if name not in SECRET_FIELD_SETS:
            raise KeyError(name)
        secrets.extend(copy.deepcopy(SECRET_FIELD_SETS[name]))
    connection.extend(copy.deepcopy(registration.get('connection_fields', [])))
    secrets.extend(copy.deepcopy(registration.get('secret_fields', [])))
    return (
        _provider_overrides(connection, 'field_id'),
        _provider_overrides(secrets, 'secret_kind'),
    )


def _provider_overrides(items, identity_key):
    """Let an explicit provider definition refine a shared field in place."""
    result = []
    positions = {}
    for item in items:
        identity = item.get(identity_key) if isinstance(item, dict) else None
        if identity in positions:
            result[positions[identity]] = item
        else:
            positions[identity] = len(result)
            result.append(item)
    return result


__all__ = ('expand_field_sets',)
