##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Shared CQL/native-protocol client foundation.

The base retains Cassandra-compatible names for API stability, while provider
subclasses own their engine identity, admitted catalog, protocol version and
administration surface. Every completion, timeout, lightweight-transaction
result and retry remains an opaque driver/server observation. Common CDEadmin
code never interprets it as transaction finality.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib
import inspect
import ipaddress
import json
import re
import shutil
import ssl
import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import date, datetime, time as datetime_time, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping
from urllib.parse import quote

from pgadmin.cdeadmin.sdk import (
    PilotProviderError,
    ProviderToolError,
    ProviderToolGrant,
    ProviderToolRunner,
)


QUALIFIED_DRIVER_VERSION = '3.30.1'
MAX_QUERY_BYTES = 2 * 1024 * 1024
MAX_PAGE_SIZE = 500
MAX_RESULT_RECORDS = 10000
MAX_ACTIVE_CURSORS = 128
MAX_IDENTITIES = 5000
IDENTITY_TTL_SECONDS = 600

_SIMPLE_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,127}$')
_JAVA_NAME = re.compile(
    r'^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*$'
)
_TYPE_TEXT = re.compile(r'^[A-Za-z][A-Za-z0-9_.,<>]*$')


class CassandraClientError(PilotProviderError):
    """A Cassandra dependency or native operation failed safely."""


class CassandraDependencyError(CassandraClientError):
    """The selected Cassandra Python driver is unavailable or unqualified."""


@dataclass
class _CassandraSession:
    cluster: object
    session: object
    keyspace: str | None
    route: dict[str, Any]
    closed: bool = False

    def close(self):
        if self.closed:
            return
        try:
            shutdown = getattr(self.session, 'shutdown', None)
            if callable(shutdown):
                shutdown()
        finally:
            shutdown = getattr(self.cluster, 'shutdown', None)
            if callable(shutdown):
                shutdown()
            self.closed = True


@dataclass
class _CassandraResult:
    future: object
    result: object
    fields: list[str]
    types: list[str]
    emitted: int = 0
    complete: bool = False
    cancelled: bool = False
    first_page: bool = True
    current_page: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _RowIdentity:
    route_fingerprint: str
    keyspace: str
    table: str
    key_columns: tuple[str, ...]
    key_values: tuple[object, ...]
    original_values: dict[str, Any]
    issued_at: float


@dataclass(frozen=True)
class _AdminCursor:
    route_fingerprint: str
    keyspace: str
    table: str
    statement: str
    parameters: tuple[object, ...]
    paging_state: bytes
    limit: int


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CassandraClientError(f'{label} must be an object')
    return copy.deepcopy(dict(value))


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CassandraClientError(f'{label} must not be empty')
    value = value.strip()
    if len(value) > 128 or any(ord(char) < 32 for char in value):
        raise CassandraClientError(f'{label} contains forbidden characters')
    return value


def _quoted(value: object, label='identifier') -> str:
    return '"' + _identifier(value, label).replace('"', '""') + '"'


def _bounded_int(value, default, minimum, maximum, label):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise CassandraClientError(f'{label} must be an integer')
    if not minimum <= value <= maximum:
        raise CassandraClientError(
            f'{label} must be between {minimum} and {maximum}'
        )
    return value


def _type_expression(value: object, label='CQL type') -> str:
    value = _identifier(value, label).replace(' ', '')
    if not _TYPE_TEXT.fullmatch(value):
        raise CassandraClientError(f'{label} is not an admitted CQL type')
    depth = 0
    for char in value:
        if char == '<':
            depth += 1
        elif char == '>':
            depth -= 1
        if depth < 0:
            raise CassandraClientError(f'{label} is malformed')
    if depth or '<>' in value or ',>' in value or '<,' in value:
        raise CassandraClientError(f'{label} is malformed')
    return value


def _literal(value: object) -> str:
    """Encode a bounded JSON-like value for CQL DDL properties."""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float, Decimal)) and not isinstance(
        value, bool
    ):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, Mapping):
        return '{' + ', '.join(
            f'{_literal(str(key))}: {_literal(item)}'
            for key, item in value.items()
        ) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(_literal(item) for item in value) + ']'
    if isinstance(value, tuple):
        return '(' + ', '.join(_literal(item) for item in value) + ')'
    raise CassandraClientError('value cannot be represented safely in CQL')


def _json_literal(value: object, label: str) -> object:
    """Decode a form-supplied JSON scalar or collection without CQL text."""
    if not isinstance(value, str) or not value.strip():
        raise CassandraClientError(f'{label} must be JSON')
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        raise CassandraClientError(f'{label} must be valid JSON') from None


class CassandraClient:
    """Synchronous, bounded adapter over Apache Cassandra Python Driver."""

    DEFAULT_CLUSTER_NAME = 'Cassandra'
    DISCOVERY_CATEGORIES = (
        ('local', (
            'SELECT cluster_name, data_center, rack, host_id, '
            'broadcast_address, listen_address, release_version, '
            'partitioner, tokens FROM system.local'
        )),
        ('peers', (
            'SELECT peer, peer_port, native_address, native_port, '
            'data_center, rack, host_id, release_version, tokens '
            'FROM system.peers_v2'
        )),
        ('keyspace', 'SELECT * FROM system_schema.keyspaces'),
        ('table', 'SELECT * FROM system_schema.tables'),
        ('column', 'SELECT * FROM system_schema.columns'),
        ('index', 'SELECT * FROM system_schema.indexes'),
        ('materialized-view', 'SELECT * FROM system_schema.views'),
        ('user-defined-type', 'SELECT * FROM system_schema.types'),
        ('function', 'SELECT * FROM system_schema.functions'),
        ('aggregate', 'SELECT * FROM system_schema.aggregates'),
        ('role', 'LIST ROLES'),
        ('permission', 'LIST ALL PERMISSIONS'),
        ('tracing-session', (
            'SELECT session_id, command, duration, request, '
            'started_at FROM system_traces.sessions'
        )),
    )
    VIRTUAL_RESOURCES = (
        ('query', 'CQL query execution'),
        ('repair', 'Repair operations'),
        ('compaction', 'Compaction operations'),
        ('snapshot', 'Snapshot operations'),
        ('backup', 'SSTable backup tools'),
        ('restore', 'SSTable restore tools'),
        ('shell', 'CQL shell'),
    )
    DISCOVERY_NAME_FIELDS = {
        'keyspace': 'keyspace_name', 'table': 'table_name',
        'column': 'column_name', 'index': 'index_name',
        'materialized-view': 'view_name',
        'user-defined-type': 'type_name', 'function': 'function_name',
        'aggregate': 'aggregate_name', 'role': 'role',
        'permission': 'permission', 'tracing-session': 'session_id',
    }

    ROUTE_KEYS = frozenset({
        'route_id', 'host', 'port', 'contact_points', 'keyspace', 'username',
        'user', 'principal_reference', 'credential_reference_id', 'local_dc',
        'tls_mode', 'compression', 'consistency', 'serial_consistency',
        'request_timeout', 'connect_timeout', 'protocol_version',
        'tool_workspace', 'jmx_port', 'tls_ca_file', 'auth_mode',
        'credential_kinds', 'credential_references',
        'tls_certificate_file', 'tls_key_file', 'tls_check_hostname',
        'tls_min_version', 'tls_ciphers', 'load_balancing_policy',
        'used_hosts_per_remote_dc', 'allow_remote_dcs_for_local_cl',
        'control_connection_timeout', 'heartbeat_interval',
        'heartbeat_timeout', 'schema_agreement_timeout',
        'reconnect_base_delay', 'reconnect_max_delay',
        'reconnect_max_attempts', 'executor_threads', 'application_name',
    })
    CONSISTENCIES = frozenset({
        'ANY', 'ONE', 'TWO', 'THREE', 'QUORUM', 'ALL', 'LOCAL_QUORUM',
        'EACH_QUORUM', 'SERIAL', 'LOCAL_SERIAL', 'LOCAL_ONE',
    })
    SERIAL_CONSISTENCIES = frozenset({'SERIAL', 'LOCAL_SERIAL'})
    ADMIN_OPERATIONS = {
        'cluster': frozenset({'inspect'}),
        'datacenter': frozenset({'inspect'}),
        'node': frozenset({'inspect'}),
        'keyspace': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'replication': frozenset({'inspect', 'alter'}),
        'table': frozenset({
            'inspect', 'create', 'alter', 'insert', 'update', 'delete',
            'drop',
        }),
        'column': frozenset({
            'inspect', 'create', 'rename', 'drop',
        }),
        'index': frozenset({'inspect', 'create', 'drop'}),
        'materialized-view': frozenset({'inspect', 'create', 'drop'}),
        'user-defined-type': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'function': frozenset({'inspect', 'create', 'drop'}),
        'aggregate': frozenset({'inspect', 'create', 'drop'}),
        'role': frozenset({
            'inspect', 'create', 'alter', 'grant', 'revoke', 'drop',
        }),
        'permission': frozenset({'inspect', 'grant', 'revoke'}),
        'query': frozenset({'inspect'}),
        'tracing-session': frozenset({'inspect'}),
        'repair': frozenset({'inspect', 'execute'}),
        'compaction': frozenset({'inspect', 'execute'}),
        'snapshot': frozenset({'inspect', 'execute'}),
        'backup': frozenset({'inspect', 'execute'}),
        'restore': frozenset({'inspect', 'execute'}),
        'shell': frozenset({'inspect', 'execute'}),
    }
    TOOL_KINDS = frozenset({
        'repair', 'compaction', 'snapshot', 'backup', 'restore', 'shell',
    })

    def __init__(self, secret_acquirer=None, module=None):
        try:
            if module is None:
                root = importlib.import_module('cassandra')
                cluster_module = importlib.import_module('cassandra.cluster')
                auth_module = importlib.import_module('cassandra.auth')
                query_module = importlib.import_module('cassandra.query')
                policies_module = importlib.import_module(
                    'cassandra.policies'
                )
                module = SimpleNamespace(**{
                    '__version__': getattr(root, '__version__', None),
                    'Cluster': cluster_module.Cluster,
                    'PlainTextAuthProvider': auth_module.PlainTextAuthProvider,
                    'ConsistencyLevel': root.ConsistencyLevel,
                    'ProtocolVersion': root.ProtocolVersion,
                    'SimpleStatement': query_module.SimpleStatement,
                    'dict_factory': query_module.dict_factory,
                    'DCAwareRoundRobinPolicy': (
                        policies_module.DCAwareRoundRobinPolicy
                    ),
                    'RoundRobinPolicy': policies_module.RoundRobinPolicy,
                    'TokenAwarePolicy': policies_module.TokenAwarePolicy,
                    'ExponentialReconnectionPolicy': (
                        policies_module.ExponentialReconnectionPolicy
                    ),
                })
        except (ImportError, ModuleNotFoundError) as exc:
            raise CassandraDependencyError(
                'Cassandra client dependency cassandra-driver is unavailable'
            ) from exc
        observed = getattr(module, '__version__', None)
        if observed is not None and str(observed) != QUALIFIED_DRIVER_VERSION:
            raise CassandraDependencyError(
                'Cassandra Python driver version is not the qualified 3.30.1'
            )
        for name in (
            'Cluster', 'PlainTextAuthProvider', 'ConsistencyLevel',
            'SimpleStatement', 'dict_factory',
        ):
            if getattr(module, name, None) is None:
                raise CassandraDependencyError(
                    f'Cassandra Python driver lacks approved {name}'
                )
        self.module = module
        self._secret_acquirer = secret_acquirer
        self._sessions: list[_CassandraSession] = []
        self._results: list[_CassandraResult] = []
        self._row_identities: dict[str, _RowIdentity] = {}
        self._admin_cursors: dict[str, _AdminCursor] = {}
        self._tool_runner = ProviderToolRunner({
            'cqlsh': 'cqlsh', 'nodetool': 'nodetool',
            'sstableloader': 'sstableloader',
        })

    @classmethod
    def _route(cls, request):
        route = _mapping(
            request.get('route', request.get('_provider_route')),
            'Cassandra route',
        )
        unknown = sorted(set(route).difference(cls.ROUTE_KEYS))
        if unknown:
            raise CassandraClientError(
                'Cassandra route contains unknown fields: ' +
                ', '.join(unknown)
            )
        if route.get('user') is not None:
            if route.get('username') not in {None, route['user']}:
                raise CassandraClientError(
                    'Cassandra route user aliases disagree'
                )
            route['username'] = route.pop('user')
        host = _identifier(route.get('host'), 'Cassandra host')
        contact_points = route.get('contact_points', [])
        if isinstance(contact_points, str):
            contact_points = [
                item.strip() for item in contact_points.split(',')
                if item.strip()
            ]
        if not isinstance(contact_points, list) or any(
            not isinstance(item, str) for item in contact_points
        ):
            raise CassandraClientError(
                'Cassandra contact points must be an array or comma list'
            )
        route['contact_points'] = list(dict.fromkeys(
            [host] + [_identifier(item, 'contact point')
                      for item in contact_points]
        ))
        route['port'] = _bounded_int(
            route.get('port'), 9042, 1, 65535, 'Cassandra port'
        )
        for name in ('keyspace', 'local_dc', 'username'):
            if route.get(name) is not None:
                route[name] = _identifier(
                    route[name], f'Cassandra {name}'
                )
        auth_mode = route.get(
            'auth_mode', 'password' if route.get('username') else 'none'
        )
        if auth_mode not in {
            'none', 'password', 'mutual-tls', 'password-mutual-tls'
        }:
            raise CassandraClientError('Cassandra auth mode is invalid')
        route['auth_mode'] = auth_mode
        references = route.get('credential_references') or {}
        if not isinstance(references, Mapping):
            raise CassandraClientError(
                'Cassandra credential references must be an object'
            )
        if route.get('credential_reference_id') is not None:
            references.setdefault(
                'database_password', route['credential_reference_id']
            )
        route['credential_references'] = dict(references)
        password_auth = auth_mode in {'password', 'password-mutual-tls'}
        if password_auth and (route.get('username') is None or
                              'database_password' not in references):
            raise CassandraClientError(
                'Cassandra password authentication requires username and '
                'credential reference'
            )
        if not password_auth and route.get('username') is not None:
            raise CassandraClientError(
                'Cassandra username requires password authentication'
            )
        tls_mode = route.get('tls_mode', 'disabled')
        if tls_mode not in {'disabled', 'system-ca', 'self-signed'}:
            raise CassandraClientError('Cassandra TLS mode is invalid')
        route['tls_mode'] = tls_mode
        for name in ('tls_ca_file', 'tls_certificate_file', 'tls_key_file'):
            if route.get(name) is None:
                continue
            path = Path(_identifier(route[name], f'Cassandra {name}'))
            if not path.is_absolute():
                raise CassandraClientError(
                    f'Cassandra {name} must be absolute'
                )
            route[name] = str(path.resolve(strict=False))
        certificate_auth = auth_mode in {
            'mutual-tls', 'password-mutual-tls'
        }
        if certificate_auth and (
            not route.get('tls_certificate_file') or
            not route.get('tls_key_file') or tls_mode == 'disabled'
        ):
            raise CassandraClientError(
                'Cassandra mutual TLS requires TLS, certificate, and key'
            )
        if bool(route.get('tls_certificate_file')) != bool(
            route.get('tls_key_file')
        ):
            raise CassandraClientError(
                'Cassandra TLS certificate and key must be supplied together'
            )
        route['tls_check_hostname'] = route.get(
            'tls_check_hostname', tls_mode == 'system-ca'
        ) is not False
        route['tls_min_version'] = route.get('tls_min_version', 'TLSv1_2')
        if route['tls_min_version'] not in {'TLSv1_2', 'TLSv1_3'}:
            raise CassandraClientError(
                'Cassandra minimum TLS version is invalid'
            )
        compression = route.get('compression', 'none')
        if compression not in {'none', 'lz4', 'snappy'}:
            raise CassandraClientError('Cassandra compression is invalid')
        route['compression'] = compression
        consistency = str(route.get('consistency', 'LOCAL_ONE')).upper()
        serial = str(
            route.get('serial_consistency', 'LOCAL_SERIAL')
        ).upper()
        if consistency not in cls.CONSISTENCIES - cls.SERIAL_CONSISTENCIES:
            raise CassandraClientError('Cassandra consistency is invalid')
        if serial not in cls.SERIAL_CONSISTENCIES:
            raise CassandraClientError(
                'Cassandra serial consistency is invalid'
            )
        route['consistency'] = consistency
        route['serial_consistency'] = serial
        route['request_timeout'] = _bounded_int(
            route.get('request_timeout'), 30, 1, 600, 'request timeout'
        )
        route['connect_timeout'] = _bounded_int(
            route.get('connect_timeout'), 10, 1, 600, 'connect timeout'
        )
        for name, default, minimum, maximum, label in (
            ('control_connection_timeout', 10, 1, 600,
             'control connection timeout'),
            ('heartbeat_interval', 30, 0, 3600, 'heartbeat interval'),
            ('heartbeat_timeout', 30, 1, 3600, 'heartbeat timeout'),
            ('schema_agreement_timeout', 10, 0, 3600,
             'schema agreement timeout'),
            ('reconnect_base_delay', 1, 1, 300, 'reconnect base delay'),
            ('reconnect_max_delay', 30, 1, 3600,
             'reconnect maximum delay'),
            ('reconnect_max_attempts', 64, 1, 100000,
             'reconnect maximum attempts'),
            ('executor_threads', 2, 1, 256, 'executor threads'),
            ('used_hosts_per_remote_dc', 0, 0, 1024,
             'remote hosts per datacenter'),
        ):
            route[name] = _bounded_int(
                route.get(name), default, minimum, maximum, label
            )
        if route['reconnect_max_delay'] < route['reconnect_base_delay']:
            raise CassandraClientError(
                'Cassandra reconnect maximum delay is below base delay'
            )
        policy = route.get('load_balancing_policy', 'token-aware-dc')
        if policy not in {
            'dc-aware', 'round-robin', 'token-aware-dc',
            'token-aware-round-robin'
        }:
            raise CassandraClientError(
                'Cassandra load-balancing policy is invalid'
            )
        route['load_balancing_policy'] = policy
        route['allow_remote_dcs_for_local_cl'] = bool(
            route.get('allow_remote_dcs_for_local_cl', False)
        )
        if route.get('application_name') is not None:
            route['application_name'] = _identifier(
                route['application_name'], 'application name'
            )
        protocol = _bounded_int(
            route.get('protocol_version'), 5, 5, 5, 'protocol version'
        )
        route['protocol_version'] = protocol
        route['jmx_port'] = _bounded_int(
            route.get('jmx_port'), 7199, 1, 65535, 'JMX port'
        )
        return route

    @staticmethod
    def _ssl_context(route, key_password=None):
        if route['tls_mode'] == 'disabled':
            return None
        if route['tls_mode'] == 'system-ca':
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            if route.get('tls_ca_file'):
                context.load_verify_locations(cafile=route['tls_ca_file'])
        else:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        context.check_hostname = (
            route['tls_mode'] == 'system-ca' and
            route.get('tls_check_hostname', True)
        )
        context.minimum_version = getattr(
            ssl.TLSVersion, route.get('tls_min_version', 'TLSv1_2')
        )
        if route.get('tls_ciphers'):
            context.set_ciphers(route['tls_ciphers'])
        if route.get('tls_certificate_file'):
            context.load_cert_chain(
                route['tls_certificate_file'], route['tls_key_file'],
                password=key_password,
            )
        return context

    def _cluster_options(self, route, auth_provider, key_password=None):
        result = {
            'contact_points': route['contact_points'],
            'port': route['port'],
            'auth_provider': auth_provider,
            'ssl_context': self._ssl_context(route, key_password),
            'compression': (
                None if route['compression'] == 'none'
                else route['compression']
            ),
            'connect_timeout': float(route['connect_timeout']),
            'control_connection_timeout': float(
                route['control_connection_timeout']
            ),
            'protocol_version': route['protocol_version'],
            'idle_heartbeat_interval': float(route['heartbeat_interval']),
            'idle_heartbeat_timeout': float(route['heartbeat_timeout']),
            'max_schema_agreement_wait': float(
                route['schema_agreement_timeout']
            ),
            'executor_threads': route['executor_threads'],
            'application_name': route.get('application_name', 'CDEadmin'),
        }
        dc_policy = getattr(self.module, 'DCAwareRoundRobinPolicy', None)
        round_robin = getattr(self.module, 'RoundRobinPolicy', None)
        token_aware = getattr(self.module, 'TokenAwarePolicy', None)
        policy_name = route['load_balancing_policy']
        if 'dc' in policy_name and callable(dc_policy):
            policy_options = {
                'local_dc': route.get('local_dc', ''),
                'used_hosts_per_remote_dc': (
                    route['used_hosts_per_remote_dc']
                ),
            }
            try:
                parameters = inspect.signature(dc_policy).parameters
            except (TypeError, ValueError):
                parameters = {}
            accepts_keywords = any(
                item.kind == inspect.Parameter.VAR_KEYWORD
                for item in parameters.values()
            )
            if ('allow_remote_dcs_for_local_cl' in parameters or
                    accepts_keywords):
                policy_options['allow_remote_dcs_for_local_cl'] = (
                    route['allow_remote_dcs_for_local_cl']
                )
            elif route['allow_remote_dcs_for_local_cl']:
                raise CassandraClientError(
                    'Cassandra driver does not support remote datacenters '
                    'for local consistency levels'
                )
            child = dc_policy(**policy_options)
        elif callable(round_robin):
            child = round_robin()
        else:
            child = None
        if policy_name.startswith('token-aware') and callable(token_aware):
            child = token_aware(child)
        if child is not None:
            result['load_balancing_policy'] = child
        reconnect = getattr(self.module, 'ExponentialReconnectionPolicy', None)
        if callable(reconnect):
            result['reconnection_policy'] = reconnect(
                route['reconnect_base_delay'], route['reconnect_max_delay'],
                route['reconnect_max_attempts'],
            )
        return result

    def _connect(self, request):
        route = self._route(request)
        references = route.get('credential_references', {})
        username = route.get('username')

        def connect(credentials=None):
            credentials = credentials or {}
            auth_provider = None
            if route['auth_mode'] in {'password', 'password-mutual-tls'}:
                auth_provider = self.module.PlainTextAuthProvider(
                    username=username,
                    password=credentials.get('database_password'),
                )
            cluster = self.module.Cluster(
                **self._cluster_options(
                    route, auth_provider,
                    credentials.get('tls_private_key_password'),
                )
            )
            try:
                session = cluster.connect(route.get('keyspace'))
                session.row_factory = self.module.dict_factory
                session.default_timeout = float(route['request_timeout'])
                return cluster, session
            except Exception:
                shutdown = getattr(cluster, 'shutdown', None)
                if callable(shutdown):
                    shutdown()
                raise

        if not references:
            cluster, session = connect()
        else:
            principal = _identifier(
                route.get('principal_reference'), 'principal reference'
            )
            if not callable(self._secret_acquirer):
                raise CassandraClientError(
                    'Cassandra credential binding is unavailable'
                )
            credentials = {}
            with ExitStack() as stack:
                for kind, reference in sorted(references.items()):
                    if kind not in {
                        'database_password', 'tls_private_key_password'
                    }:
                        raise CassandraClientError(
                            'Cassandra credential kind is unsupported'
                        )
                    lease = stack.enter_context(self._secret_acquirer(
                        _identifier(reference, 'credential reference'),
                        principal, 'connect', kind,
                    ))
                    credentials[kind] = lease.use(
                        lambda value: bytes(value).decode('utf-8')
                    )
                cluster, session = connect(credentials)
        return cluster, session, route

    def runtime_identity(self, request, handle=None):
        owned = handle is None
        cluster = None
        session = None
        try:
            if handle is None:
                cluster, session, _route = self._connect(request)
            else:
                cluster, session = handle.cluster, handle.session
            rows = self._rows(session, (
                'SELECT cluster_name, release_version, cql_version, '
                'native_protocol_version, data_center, rack, partitioner, '
                'host_id FROM system.local'
            ))
            row = rows[0] if rows else {}
            version = str(row.get('release_version') or '')
            if not version:
                raise CassandraClientError(
                    'Cassandra runtime omitted its release version'
                )
            return {
                'engine_id': 'cassandra', 'version': version,
                'build_id': (
                    f'{row.get("cluster_name", "unknown")}:'
                    f'{row.get("partitioner", "unknown")}'
                ),
                'protocol_id': 'cql',
                'native': {
                    'cluster_name': row.get('cluster_name'),
                    'cql_version': row.get('cql_version'),
                    'native_protocol_version': row.get(
                        'native_protocol_version'
                    ),
                    'data_center': row.get('data_center'),
                    'rack': row.get('rack'),
                    'host_id': row.get('host_id'),
                    'driver_version': QUALIFIED_DRIVER_VERSION,
                },
            }
        except CassandraClientError:
            raise
        except Exception as exc:
            raise CassandraClientError(
                f'Cassandra runtime identity failed ({type(exc).__name__})'
            ) from None
        finally:
            if owned and cluster is not None:
                self._close_native(cluster, session)

    def open_session(self, request):
        try:
            cluster, session, route = self._connect(request)
            handle = _CassandraSession(
                cluster, session, route.get('keyspace'), route
            )
            self._sessions.append(handle)
            return handle
        except CassandraClientError:
            raise
        except Exception as exc:
            raise CassandraClientError(
                f'Cassandra session open failed ({type(exc).__name__})'
            ) from None

    @staticmethod
    def describe_transaction(handle):
        return {
            'native_boundary': 'cassandra-cql-operation',
            'keyspace': handle.keyspace,
            'multi_statement_acid_transaction_exposed': False,
            'batch_atomicity_scope': 'cassandra-driver-and-server-owned',
            'lightweight_transaction_outcome': (
                'cassandra-driver-and-server-owned'
            ),
            'consistency_outcome': 'cassandra-driver-and-server-owned',
            'common_finality_inference': False,
            'retry_decision_owned_by_common_code': False,
        }

    def _consistency(self, name):
        value = getattr(self.module.ConsistencyLevel, name, None)
        if value is None:
            raise CassandraClientError(
                f'Cassandra driver lacks consistency {name}'
            )
        return value

    def _statement(self, source, route, fetch_size=MAX_PAGE_SIZE):
        return self.module.SimpleStatement(
            source,
            consistency_level=self._consistency(route['consistency']),
            serial_consistency_level=self._consistency(
                route['serial_consistency']
            ),
            fetch_size=fetch_size,
        )

    def execute(self, handle, request):
        source = request.get('source')
        if not isinstance(source, str) or not source.strip():
            raise CassandraClientError('CQL source must not be empty')
        if len(source.encode('utf-8')) > MAX_QUERY_BYTES:
            raise CassandraClientError('CQL source exceeds the safety limit')
        parameters = request.get('parameters', ())
        if not isinstance(parameters, (Mapping, list, tuple)):
            raise CassandraClientError(
                'CQL parameters must be an object or array'
            )
        try:
            statement = self._statement(source, handle.route)
            future = handle.session.execute_async(statement, parameters)
            # cassandra-driver applies ``Session.default_timeout`` to the
            # future. ResponseFuture.result() deliberately accepts no timeout
            # argument in the qualified 3.30.1 API.
            result = future.result()
            fields, types = self._result_schema(result)
            token = _CassandraResult(future, result, fields, types)
            self._results.append(token)
            return token
        except CassandraClientError:
            raise
        except Exception as exc:
            raise CassandraClientError(
                f'Cassandra execution failed ({type(exc).__name__})'
            ) from None

    def cancel(self, token):
        if token.complete or token.cancelled:
            return False
        cancel = getattr(token.future, 'cancel', None)
        accepted = bool(cancel()) if callable(cancel) else False
        token.cancelled = accepted
        token.complete = accepted
        return accepted

    def describe_result(self, token):
        if not token.complete:
            if not token.first_page:
                fetch = getattr(token.result, 'fetch_next_page', None)
                if callable(fetch):
                    fetch()
            rows = list(getattr(token.result, 'current_rows', ()) or ())
            remaining = MAX_RESULT_RECORDS - token.emitted
            token.current_page = [
                self._json_value(self._row_mapping(row))
                for row in rows[:min(MAX_PAGE_SIZE, remaining)]
            ]
            token.emitted += len(token.current_page)
            token.first_page = False
            has_more = bool(getattr(token.result, 'has_more_pages', False))
            token.complete = (
                not has_more or token.emitted >= MAX_RESULT_RECORDS
            )
        warnings = list(getattr(token.future, 'warnings', ()) or ())
        trace_id = getattr(token.future, 'trace_id', None)
        return {
            'result_kind': 'wide_column',
            'schema': {
                'columns': [
                    {'name': name, 'type': token.types[index]}
                    for index, name in enumerate(token.fields)
                ],
                'native_observation': {
                    'warnings': [str(item) for item in warnings],
                    'trace_id': str(trace_id) if trace_id else None,
                    'cancelled': token.cancelled,
                    'common_finality_inference': False,
                },
            },
            'complete': token.complete,
            'stream_reference': (
                None if token.complete else 'provider-retained'
            ),
            'payload': {'rows': copy.deepcopy(token.current_page)},
        }

    @staticmethod
    def _result_schema(result):
        names = list(getattr(result, 'column_names', ()) or ())
        types = [str(item) for item in (
            getattr(result, 'column_types', ()) or ()
        )]
        if not names:
            rows = list(getattr(result, 'current_rows', ()) or ())
            if rows:
                names = list(CassandraClient._row_mapping(rows[0]))
        if len(types) < len(names):
            types.extend(['cql-value'] * (len(names) - len(types)))
        return [str(item) for item in names], types

    @staticmethod
    def _row_mapping(row):
        if isinstance(row, Mapping):
            return dict(row)
        as_dict = getattr(row, '_asdict', None)
        if callable(as_dict):
            return dict(as_dict())
        return {'value': row}

    @classmethod
    def _json_value(cls, value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, bytes):
            return {
                '$binary': base64.b64encode(value).decode('ascii'),
                '$encoding': 'base64',
            }
        if isinstance(value, (datetime, date, datetime_time, timedelta)):
            return {'$temporal': type(value).__name__, '$value': str(value)}
        if isinstance(value, Decimal):
            return {'$decimal': str(value)}
        if isinstance(value, (uuid.UUID, ipaddress.IPv4Address,
                              ipaddress.IPv6Address)):
            return {'$type': type(value).__name__, '$value': str(value)}
        if isinstance(value, Mapping):
            return {
                str(key): cls._json_value(item)
                for key, item in value.items()
            }
        as_dict = getattr(value, '_asdict', None)
        if callable(as_dict):
            return {
                '$udt': {
                    str(key): cls._json_value(item)
                    for key, item in as_dict().items()
                }
            }
        tolist = getattr(value, 'tolist', None)
        if callable(tolist):
            return {'$vector': cls._json_value(tolist())}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [cls._json_value(item) for item in value]
        return {'$driver_value': str(value)}

    @staticmethod
    def _close_native(cluster, session):
        if session is not None:
            shutdown = getattr(session, 'shutdown', None)
            if callable(shutdown):
                shutdown()
        shutdown = getattr(cluster, 'shutdown', None)
        if callable(shutdown):
            shutdown()

    def _read(self, request, source, parameters=()):
        cluster, session, route = self._connect(request)
        try:
            statement = self._statement(source, route, MAX_PAGE_SIZE)
            return self._rows(session, statement, parameters)[:2000]
        finally:
            self._close_native(cluster, session)

    @classmethod
    def _rows(cls, session, statement, parameters=(), **options):
        result = session.execute(statement, parameters, **options)
        return [cls._json_value(cls._row_mapping(row)) for row in result]

    @staticmethod
    def _resource(kind, name, native, parent=None):
        identity = ':'.join(str(native.get(key, '')) for key in (
            'keyspace_name', 'table_name', 'column_name', 'index_name',
            'view_name', 'type_name', 'function_name', 'aggregate_name',
            'name', 'host_id', 'role', 'permission', 'session_id',
        )) or str(name)
        encoded = quote(identity, safe='')
        generation = hashlib.sha256(json.dumps(
            native, sort_keys=True, default=str
        ).encode('utf-8')).hexdigest()[:20]
        path = ['cassandra']
        if parent:
            path.extend(parent if isinstance(parent, list) else [parent])
        path.extend([kind, str(name)])
        return {
            'resource_id': f'cassandra:{kind}:{encoded}',
            'resource_kind': kind,
            'display_name': str(name),
            'display_path': path,
            'authority_path': path,
            'generation': generation,
            'native': copy.deepcopy(native),
        }

    def list_resources(self, request):
        resources = []
        local_rows = []
        discovered = {}
        topology_rows = []
        cluster = session = None
        try:
            cluster, session, route = self._connect(request)
            all_hosts = getattr(
                getattr(cluster, 'metadata', None), 'all_hosts', None
            )
            if callable(all_hosts):
                for host in all_hosts():
                    topology_rows.append(self._json_value({
                        'native_address': getattr(host, 'address', None),
                        'native_port': route['port'],
                        'data_center': getattr(host, 'datacenter', None),
                        'rack': getattr(host, 'rack', None),
                        'host_id': getattr(host, 'host_id', None),
                        'release_version': getattr(
                            host, 'release_version', None
                        ),
                        'is_up': getattr(host, 'is_up', None),
                    }))
            for kind, statement in self.DISCOVERY_CATEGORIES:
                try:
                    rows = self._rows(
                        session,
                        self._statement(statement, route, MAX_PAGE_SIZE),
                    )[:2000]
                except Exception:
                    rows = []
                discovered[kind] = rows
                if kind == 'local':
                    local_rows = rows
        finally:
            if cluster is not None:
                self._close_native(cluster, session)
        columns_by_table = {}
        for column in discovered.get('column', []):
            table_key = (
                column.get('keyspace_name'), column.get('table_name')
            )
            columns_by_table.setdefault(table_key, []).append(column)
        for table in discovered.get('table', []):
            table_columns = columns_by_table.get((
                table.get('keyspace_name'), table.get('table_name')
            ), [])
            table['columns'] = sorted(
                table_columns,
                key=lambda item: (
                    0 if item.get('kind') == 'partition_key' else 1,
                    0 if item.get('kind') == 'clustering' else 1,
                    item.get('position', 0), item.get('column_name', ''),
                ),
            )
            table['primary_key'] = [
                item.get('column_name') for item in table['columns']
                if item.get('kind') in {'partition_key', 'clustering'}
            ]
        local = local_rows[0] if local_rows else {
            'cluster_name': self.DEFAULT_CLUSTER_NAME,
        }
        cluster_name = local.get('cluster_name') or self.DEFAULT_CLUSTER_NAME
        resources.append(self._resource(
            'cluster', cluster_name, local
        ))
        datacenters = set()
        node_rows = topology_rows or (
            discovered.get('local', []) + discovered.get('peers', [])
        )
        for row in node_rows:
            dc = row.get('data_center')
            if dc:
                datacenters.add(str(dc))
            host = row.get('host_id') or row.get('native_address') or row.get(
                'broadcast_address'
            )
            if host:
                resources.append(self._resource(
                    'node', host, row, ['cluster', cluster_name]
                ))
        for dc in sorted(datacenters):
            resources.append(self._resource(
                'datacenter', dc, {'name': dc, 'data_center': dc},
                ['cluster', cluster_name],
            ))
        for kind, field_name in self.DISCOVERY_NAME_FIELDS.items():
            for row in discovered.get(kind, []):
                name = row.get(field_name)
                if name is None:
                    continue
                parent = []
                if row.get('keyspace_name'):
                    parent.extend(['keyspace', row['keyspace_name']])
                if row.get('table_name'):
                    parent.extend(['table', row['table_name']])
                resources.append(self._resource(kind, name, row, parent))
        for row in discovered.get('keyspace', []):
            name = row.get('keyspace_name')
            if not name:
                continue
            resources.append(self._resource(
                'replication', name, {
                    'name': name,
                    'keyspace_name': name,
                    'replication': copy.deepcopy(row.get('replication', {})),
                    'durable_writes': row.get('durable_writes'),
                }, ['keyspace', name],
            ))
        for kind, name in self.VIRTUAL_RESOURCES:
            item = self._resource(
                kind, name, {'name': name, 'tool_resource': True}
            )
            item['is_virtual'] = True
            resources.append(item)
        return resources

    def inspect_resource(self, request):
        resource_id = request.get('resource_id')
        for resource in self.list_resources(request):
            if resource['resource_id'] == resource_id:
                return resource
        raise CassandraClientError('Cassandra resource is unavailable')

    def describe_security(self, request):
        try:
            roles = self._read(request, 'LIST ROLES')
        except Exception:
            roles = []
        try:
            permissions = self._read(request, 'LIST ALL PERMISSIONS')
        except Exception:
            permissions = []
        return {
            'resource_id': 'cassandra:security:current',
            'display_name': 'Cassandra roles and permissions',
            'authority_path': ['cassandra', 'security', 'current'],
            'generation': hashlib.sha256(json.dumps(
                [roles, permissions], sort_keys=True, default=str
            ).encode('utf-8')).hexdigest()[:20],
            'native': {
                'authorization_model': 'cassandra-native-rbac',
                'roles': roles, 'permissions': permissions,
            },
        }

    def supports_admin_operation(self, resource_kind, operation_id):
        return operation_id in self.ADMIN_OPERATIONS.get(
            resource_kind, frozenset()
        )

    @staticmethod
    def _form(form_id, title, fields):
        return {'form_id': form_id, 'title': title, 'fields': fields}

    @staticmethod
    def _field(field_id, label, control='text', required=False, **values):
        return {
            'field_id': field_id, 'label': label, 'control': control,
            'required': required, **values,
        }

    def visual_admin_catalog(self, catalog):
        catalog['native_planner'] = 'cassandra-cql-structured-planner'
        catalog['query_language'] = 'CQL 3'
        catalog['partition_identity'] = (
            'partition-key-plus-clustering-columns'
        )
        catalog['transaction_authority'] = 'cassandra-driver-and-server'
        catalog['native_outcomes_are_opaque'] = True
        catalog['experience_families'] = ['wide_column']

        def declaration(*resource_kinds):
            return {
                'status': 'supported',
                'resource_kinds': list(resource_kinds),
                'operation_obligations': {
                    kind: sorted(self.ADMIN_OPERATIONS[kind])
                    for kind in resource_kinds
                },
                'reason': (
                    'Cassandra wide-column objects and operations are '
                    'provider-owned through CQL v5 and bounded nodetool '
                    'administration.'
                ),
                'evidence': ['cassandra-5.0.8-native-wide-column-catalog'],
            }

        if catalog.get('engine_id') == 'cassandra':
            catalog['concept_declarations'] = {'wide_column': {
                'keyspaces': declaration('keyspace'),
                'tables': declaration('table'),
                'columns': declaration('column'),
                'types': declaration('user-defined-type'),
                'materialized_views': declaration('materialized-view'),
                'replication_and_compaction': declaration(
                    'replication', 'compaction'
                ),
            }}
        for resource in catalog.get('objects', []):
            kind = resource['resource_kind']
            resource['operations'] = [
                operation for operation in resource.get('operations', [])
                if self.supports_admin_operation(
                    kind, operation['operation_id']
                )
            ]
            for operation in resource.get('operations', []):
                op = operation['operation_id']
                form = self._admin_form(kind, op)
                if form is not None:
                    operation['form'] = form
                if kind in self.TOOL_KINDS:
                    operation['required_permissions'] = [
                        'filesystem', 'execute',
                    ]
        return catalog

    def _admin_form(self, kind, operation):
        f = self._field
        if operation == 'inspect':
            return self._form('cassandra-inspect', 'Inspect', [])
        if kind == 'keyspace' and operation in {'create', 'alter'}:
            fields = [] if operation == 'alter' else [
                f('name', 'Keyspace name', required=True)
            ]
            fields.extend([
                f('replication', 'Replication strategy', 'json', True,
                  default={'class': 'NetworkTopologyStrategy'}),
                f('durable_writes', 'Durable writes', 'boolean', False,
                  default=True),
            ])
            return self._form(f'cassandra-keyspace-{operation}',
                              f'{operation.title()} keyspace', fields)
        if kind == 'replication' and operation == 'alter':
            return self._form(
                'cassandra-replication-alter',
                'Alter keyspace replication', [
                    f('replication', 'Replication strategy', 'json', True,
                      default={'class': 'NetworkTopologyStrategy'}),
                    f('durable_writes', 'Durable writes', 'boolean', False,
                      default=True),
                ]
            )
        if kind == 'table' and operation == 'create':
            return self._form('cassandra-table-create', 'Create table', [
                f('keyspace', 'Keyspace', required=True),
                f('name', 'Table name', required=True),
                f('columns', 'Columns', 'json', True, default=[]),
                f('partition_keys', 'Partition key columns', 'json', True,
                  default=[]),
                f('clustering_keys', 'Clustering columns', 'json', False,
                  default=[]),
                f('options', 'Table options', 'json', False, default={}),
            ])
        if kind == 'table' and operation == 'alter':
            return self._form('cassandra-table-alter', 'Alter table', [
                f('changes', 'Column and option changes', 'json', True,
                  default={}),
            ])
        if kind == 'column' and operation in {
            'create', 'alter', 'rename'
        }:
            fields = {
                'create': [f('name', 'Column name', required=True),
                           f('type', 'CQL type', required=True)],
                'alter': [f('type', 'New CQL type', required=True)],
                'rename': [f('new_name', 'New column name', required=True)],
            }[operation]
            return self._form(f'cassandra-column-{operation}',
                              f'{operation.title()} column', fields)
        if kind == 'index' and operation == 'create':
            return self._form('cassandra-index-create', 'Create index', [
                f('keyspace', 'Keyspace', required=True),
                f('table', 'Table', required=True),
                f('name', 'Index name', required=True),
                f('target', 'Column or collection target', required=True),
                f('index_kind', 'Index kind', 'select', True, default='sai',
                  options=[
                      {'value': 'sai', 'label': 'Storage-attached index'},
                      {'value': 'secondary', 'label': 'Secondary index'},
                      {'value': 'custom', 'label': 'Custom index'},
                  ]),
                f('index_class', 'Custom index class', required=False),
                f('options', 'Index options', 'json', False, default={}),
            ])
        if kind == 'materialized-view' and operation == 'create':
            return self._form(
                'cassandra-materialized-view-create',
                'Create materialized view', [
                    f('keyspace', 'Keyspace', required=True),
                    f('name', 'View name', required=True),
                    f('base_table', 'Base table', required=True),
                    f('select_columns', 'Selected columns', 'json', True,
                      default=[]),
                    f('not_null_columns', 'Required non-null columns',
                      'json', True, default=[]),
                    f('partition_keys', 'Partition key columns', 'json', True,
                      default=[]),
                    f('clustering_keys', 'Clustering columns', 'json', False,
                      default=[]),
                    f('options', 'View options', 'json', False, default={}),
                ])
        if kind == 'user-defined-type' and operation in {'create', 'alter'}:
            fields = [] if operation == 'alter' else [
                f('keyspace', 'Keyspace', required=True),
                f('name', 'Type name', required=True),
            ]
            fields.append(f('fields', 'Fields', 'json', True, default=[]))
            return self._form(f'cassandra-type-{operation}',
                              f'{operation.title()} type', fields)
        if kind == 'function' and operation == 'create':
            return self._form('cassandra-function-create',
                              'Create function', [
                                  f('keyspace', 'Keyspace', required=True),
                                  f('name', 'Function name', required=True),
                                  f('arguments', 'Arguments', 'json', True,
                                    default=[]),
                                  f('return_type', 'Return CQL type',
                                    required=True),
                                  f('language', 'Function language',
                                    required=True),
                                  f('called_on_null_input',
                                    'Called on null input', 'boolean', False,
                                    default=False),
                                  f('body', 'Function body', 'code', True,
                                    max_length=1024 * 1024),
                              ])
        if kind == 'aggregate' and operation == 'create':
            return self._form('cassandra-aggregate-create',
                              'Create aggregate', [
                                  f('keyspace', 'Keyspace', required=True),
                                  f('name', 'Aggregate name', required=True),
                                  f('argument_types', 'Argument CQL types',
                                    'json', True, default=[]),
                                  f('state_function', 'State function',
                                    required=True),
                                  f('state_type', 'State CQL type',
                                    required=True),
                                  f('final_function', 'Final function',
                                    required=False),
                                  f('initial_condition',
                                    'Initial condition (JSON value)',
                                    'code', False, max_length=65536),
                              ])
        if kind == 'role' and operation in {'create', 'alter'}:
            fields = [] if operation == 'alter' else [
                f('name', 'Role name', required=True)
            ]
            fields.extend([
                f('login', 'Can log in', 'boolean', False, default=False),
                f('superuser', 'Superuser', 'boolean', False, default=False),
                f('password_credential_reference',
                  'Password credential reference', 'secret-reference',
                  False, sensitive=True),
                f('options', 'Role options', 'json', False, default={}),
            ])
            return self._form(f'cassandra-role-{operation}',
                              f'{operation.title()} role', fields)
        if kind in {'role', 'permission'} and operation in {'grant', 'revoke'}:
            return self._form(
                f'cassandra-{kind}-{operation}',
                f'{operation.title()} {kind}', [
                    f('principal', 'Role', required=True),
                    f('privileges', 'Permissions or roles', 'json', True,
                      default=[]),
                    f('resource', 'Cassandra resource', 'json', False,
                      default={'kind': 'all-keyspaces'}),
                    f('confirmation', 'Confirmation', required=False),
                ])
        if kind in self.TOOL_KINDS and operation == 'execute':
            return self._form(
                f'cassandra-{kind}-execute', f'Run {kind} operation', [
                    f('action', 'Action', required=True),
                    f('arguments', 'Arguments', 'json', False, default={}),
                    f('confirmation', 'Confirmation', required=False),
                ])
        return None

    def validate_admin_operation(self, request):
        errors = []
        kind = request['resource_kind']
        operation = request['operation_id']
        draft = request.get('draft', {})
        try:
            if kind == 'table' and operation == 'create':
                columns = draft.get('columns')
                partition = draft.get('partition_keys')
                if not isinstance(columns, list) or not columns:
                    raise CassandraClientError('columns must not be empty')
                if not isinstance(partition, list) or not partition:
                    raise CassandraClientError(
                        'partition keys must not be empty'
                    )
                names = []
                for column in columns:
                    item = _mapping(column, 'column')
                    names.append(_identifier(item.get('name'), 'column name'))
                    _type_expression(item.get('type'))
                if len(names) != len(set(names)) or not set(
                    partition
                ).issubset(names):
                    raise CassandraClientError(
                        'column and partition-key definitions disagree'
                    )
            if kind == 'function' and operation == 'create' and '$$' in str(
                draft.get('body', '')
            ):
                raise CassandraClientError(
                    'function body cannot contain the CQL $$ delimiter'
                )
            if (kind == 'aggregate' and operation == 'create' and
                    'initial_condition' in draft):
                _json_literal(
                    draft['initial_condition'], 'aggregate initial condition'
                )
        except CassandraClientError as exc:
            errors.append({
                'field_id': None, 'code': 'cassandra_validation',
                'message': str(exc),
            })
        return {'errors': errors}

    def plan_admin_operation(self, request):
        native = self._native_target(request.get('target_resource')) \
            if request.get('target_resource') else {}
        draft = copy.deepcopy(request.get('draft', {}))
        payload = {
            'resource_kind': request['resource_kind'],
            'operation_id': request['operation_id'],
            'draft': draft,
            'native': native,
            '_provider_route': copy.deepcopy(request.get('_provider_route')),
        }
        if request['resource_kind'] in self.TOOL_KINDS:
            compiled = {'statements': []}
            tool_preview = {
                'provider_tool': request['resource_kind'],
                'action': draft.get('action'),
                'arguments': copy.deepcopy(draft.get('arguments', {})),
            }
        else:
            compiled = self._compile_admin(payload, preview=True)
            tool_preview = None
        warnings = []
        if len(compiled.get('statements', [])) > 1:
            warnings.append(
                'Cassandra schema statements are independent native '
                'operations and are not a common-layer transaction.'
            )
        return {
            'command_preview': {
                'driver': 'apache-cassandra-python-driver',
                'language': 'CQL',
                'resource_kind': request['resource_kind'],
                'operation': request['operation_id'],
                'target': native,
                'statements': [
                    item['source'] for item in compiled.get('statements', [])
                ],
                'tool': tool_preview,
                'values_parameterized': all(
                    item.get('values_parameterized', False)
                    for item in compiled.get('statements', [])
                ),
                'credential_references_redacted': True,
            },
            'provider_payload': payload,
            'warnings': warnings,
            'receipt': {
                'provider_owned': True,
                'common_transaction_finality_inference': False,
            },
        }

    def apply_admin_operation(self, request):
        payload = _mapping(request.get('provider_payload'), 'provider plan')
        route = self._route({'route': payload.pop('_provider_route', None)})
        if payload['resource_kind'] in self.TOOL_KINDS:
            return self._apply_tool(payload, route)
        cluster = None
        session = None
        observations = []
        try:
            cluster, session, _actual_route = self._connect({'route': route})
            payload['_provider_route'] = copy.deepcopy(route)
            compiled = self._compile_admin(payload, preview=False)
            for item in compiled.get('statements', []):
                result = self._execute_admin_statement(
                    session, route, item, payload
                )
                observations.append({
                    'rows': [
                        self._json_value(self._row_mapping(row))
                        for row in list(result)[:MAX_PAGE_SIZE]
                    ],
                    'was_applied': self._was_applied(result),
                    'warnings': [str(value) for value in (
                        getattr(result, 'warnings', ()) or ()
                    )],
                })
            metadata = getattr(cluster, 'metadata', None)
            agreement = getattr(metadata, 'check_schema_agreement', None)
            schema_agreement = (
                bool(agreement()) if callable(agreement) else None
            )
            return {
                'accepted': True,
                'statement_observations': observations,
                'schema_agreement': schema_agreement,
                'driver_observation_only': True,
                'transaction_finality_interpreted_by_common_code': False,
            }
        except CassandraClientError:
            raise
        except Exception as exc:
            raise CassandraClientError(
                'Cassandra visual administration failed '
                f'({type(exc).__name__})'
            ) from None
        finally:
            if cluster is not None:
                self._close_native(cluster, session)

    @staticmethod
    def _was_applied(result):
        """Return the driver's LWT observation only when it exists.

        cassandra-driver exposes ``ResultSet.was_applied`` as a property that
        raises RuntimeError for ordinary DDL/DML result sets. Merely probing
        the property therefore has to remain guarded and must not turn a
        successful non-LWT operation into a provider failure.
        """
        try:
            return getattr(result, 'was_applied', None)
        except RuntimeError:
            return None

    def _execute_admin_statement(self, session, route, item, payload):
        sensitive = item.get('sensitive_password_reference')
        if sensitive is None:
            statement = self._statement(item['source'], route)
            return session.execute(statement, item.get('parameters', ()))
        if not callable(self._secret_acquirer):
            raise CassandraClientError(
                'Cassandra role credential binding is unavailable'
            )
        reference = _identifier(sensitive, 'role password reference')
        principal = _identifier(
            route.get('principal_reference'), 'principal reference'
        )
        lease = self._secret_acquirer(
            reference, principal, 'administer_role', 'database_password'
        )
        with lease:
            return lease.use(lambda value: session.execute(
                self._statement(
                    item['source'].replace(
                        '<CDEADMIN_PASSWORD>',
                        _literal(bytes(value).decode('utf-8')),
                    ), route,
                ),
                item.get('parameters', ()),
            ))

    def _compile_admin(self, payload, preview=False):
        kind = payload['resource_kind']
        operation = payload['operation_id']
        draft = payload.get('draft', {})
        native = payload.get('native', {})
        if operation == 'inspect':
            return {'statements': self._inspect_statements(kind, native)}
        if kind in {'keyspace', 'replication'}:
            return {'statements': [self._keyspace_statement(
                'alter' if kind == 'replication' else operation,
                draft, native
            )]}
        if kind == 'table':
            return {'statements': self._table_statements(
                operation, draft, native, payload.get('_provider_route'),
                preview,
            )}
        if kind == 'column':
            return {'statements': [self._column_statement(
                operation, draft, native
            )]}
        if kind == 'index':
            return {'statements': [self._index_statement(
                operation, draft, native
            )]}
        if kind == 'materialized-view':
            return {'statements': [self._view_statement(
                operation, draft, native
            )]}
        if kind == 'user-defined-type':
            return {'statements': [self._type_statement(
                operation, draft, native
            )]}
        if kind == 'function':
            return {'statements': [self._function_statement(
                operation, draft, native
            )]}
        if kind == 'aggregate':
            return {'statements': [self._aggregate_statement(
                operation, draft, native
            )]}
        if kind in {'role', 'permission'}:
            return {'statements': self._security_statements(
                kind, operation, draft, native, preview
            )}
        raise CassandraClientError(
            'Cassandra administration operation is unavailable'
        )

    @staticmethod
    def _target_name(native, key, label):
        value = native.get(key)
        return _identifier(value, label)

    def _keyspace_statement(self, operation, draft, native):
        name = (
            _identifier(draft.get('name'), 'keyspace name')
            if operation == 'create'
            else self._target_name(native, 'keyspace_name', 'keyspace')
        )
        target = _quoted(name, 'keyspace')
        if operation in {'create', 'alter'}:
            replication = _mapping(
                draft.get('replication'), 'replication strategy'
            )
            if not isinstance(replication.get('class'), str):
                raise CassandraClientError(
                    'replication strategy requires class'
                )
            source = (
                f'{operation.upper()} KEYSPACE '
                f'{"IF NOT EXISTS " if operation == "create" else ""}'
                f'{target} WITH replication = {_literal(replication)} '
                f'AND durable_writes = '
                f'{_literal(bool(draft.get("durable_writes", True)))}'
            )
            return {'source': source, 'parameters': (),
                    'values_parameterized': False}
        if operation == 'drop':
            return {'source': f'DROP KEYSPACE {target}', 'parameters': (),
                    'values_parameterized': True}
        raise CassandraClientError('keyspace operation is unavailable')

    def _table_statements(self, operation, draft, native, route=None,
                          preview=False):
        if operation == 'create':
            keyspace = _identifier(draft.get('keyspace'), 'keyspace')
            table = _identifier(draft.get('name'), 'table')
            columns = [_mapping(item, 'column')
                       for item in draft.get('columns', [])]
            names = [_identifier(item.get('name'), 'column')
                     for item in columns]
            if not columns or len(names) != len(set(names)):
                raise CassandraClientError(
                    'table columns must be unique and non-empty'
                )
            partition = self._name_array(
                draft.get('partition_keys'), 'partition keys'
            )
            clustering = self._name_array(
                draft.get('clustering_keys', []), 'clustering keys'
            )
            if not partition or not set(
                partition + clustering
            ).issubset(names):
                raise CassandraClientError(
                    'table key columns must exist in columns'
                )
            definitions = [
                f'{_quoted(item["name"], "column")} '
                f'{_type_expression(item.get("type"))}'
                for item in columns
            ]
            partition_source = (
                _quoted(partition[0]) if len(partition) == 1 else
                '(' + ', '.join(_quoted(item) for item in partition) + ')'
            )
            key_source = partition_source
            if clustering:
                key_source += ', ' + ', '.join(
                    _quoted(item) for item in clustering
                )
            definitions.append(f'PRIMARY KEY ({key_source})')
            source = (
                f'CREATE TABLE IF NOT EXISTS {_quoted(keyspace)}.'
                f'{_quoted(table)} ({", ".join(definitions)})'
            )
            options = draft.get('options', {})
            if options:
                source += self._with_options(options)
            return [{'source': source, 'parameters': (),
                     'values_parameterized': False}]
        keyspace, table = self._table_target(native)
        target = f'{_quoted(keyspace)}.{_quoted(table)}'
        if operation == 'drop':
            return [{'source': f'DROP TABLE {target}', 'parameters': (),
                     'values_parameterized': True}]
        if operation == 'insert':
            values = _mapping(draft.get('values'), 'insert values')
            if not values:
                raise CassandraClientError('insert values must not be empty')
            columns = [_identifier(item, 'column') for item in values]
            options = _mapping(draft.get('options', {}), 'write options')
            using = self._using_options(options)
            source = (
                f'INSERT INTO {target} '
                f'({", ".join(_quoted(item) for item in columns)}) VALUES '
                f'({", ".join("%s" for _item in columns)}){using}'
            )
            if options.get('if_not_exists'):
                source += ' IF NOT EXISTS'
            return [{'source': source,
                     'parameters': tuple(values[item] for item in columns),
                     'values_parameterized': True}]
        if operation in {'update', 'delete'}:
            identity = self._consume_identity(
                draft, keyspace, table, route, consume=not preview
            )
            predicates = ' AND '.join(
                f'{_quoted(name)} = %s' for name in identity.key_columns
            )
            if operation == 'delete':
                return [{'source': f'DELETE FROM {target} WHERE {predicates}',
                         'parameters': identity.key_values,
                         'values_parameterized': True}]
            changes = _mapping(draft.get('changes'), 'row changes')
            if not changes:
                raise CassandraClientError('row changes must not be empty')
            if set(changes).intersection(identity.key_columns):
                raise CassandraClientError(
                    'primary key columns cannot be updated'
                )
            names = [_identifier(item, 'column') for item in changes]
            source = (
                f'UPDATE {target} SET ' + ', '.join(
                    f'{_quoted(item)} = %s' for item in names
                ) + f' WHERE {predicates}'
            )
            return [{'source': source,
                     'parameters': tuple(changes[item] for item in names) +
                     identity.key_values,
                     'values_parameterized': True}]
        if operation == 'alter':
            changes = _mapping(draft.get('changes'), 'table changes')
            statements = []
            for item in changes.get('add_columns', []):
                value = _mapping(item, 'added column')
                statements.append({
                    'source': f'ALTER TABLE {target} ADD '
                    f'{_quoted(value.get("name"))} '
                    f'{_type_expression(value.get("type"))}',
                    'parameters': (), 'values_parameterized': True,
                })
            for item in changes.get('alter_columns', []):
                value = _mapping(item, 'altered column')
                statements.append({
                    'source': f'ALTER TABLE {target} ALTER '
                    f'{_quoted(value.get("name"))} TYPE '
                    f'{_type_expression(value.get("type"))}',
                    'parameters': (), 'values_parameterized': True,
                })
            for name in changes.get('drop_columns', []):
                statements.append({
                    'source': f'ALTER TABLE {target} DROP {_quoted(name)}',
                    'parameters': (), 'values_parameterized': True,
                })
            for old, new in _mapping(
                changes.get('rename_columns', {}), 'renamed columns'
            ).items():
                statements.append({
                    'source': f'ALTER TABLE {target} RENAME '
                    f'{_quoted(old)} TO {_quoted(new)}',
                    'parameters': (), 'values_parameterized': True,
                })
            if changes.get('options'):
                statements.append({
                    'source': f'ALTER TABLE {target}' +
                    self._with_options(changes['options']),
                    'parameters': (), 'values_parameterized': False,
                })
            if not statements:
                raise CassandraClientError(
                    'table changes contain no admitted operation'
                )
            return statements
        raise CassandraClientError('table operation is unavailable')

    def _column_statement(self, operation, draft, native):
        keyspace, table = self._table_target(native)
        column = (
            self._target_name(native, 'column_name', 'column')
            if operation != 'create'
            else _identifier(draft.get('name'), 'column')
        )
        target = f'{_quoted(keyspace)}.{_quoted(table)}'
        if operation == 'create':
            source = f'ALTER TABLE {target} ADD {_quoted(column)} ' \
                f'{_type_expression(draft.get("type"))}'
        elif operation == 'alter':
            source = f'ALTER TABLE {target} ALTER {_quoted(column)} TYPE ' \
                f'{_type_expression(draft.get("type"))}'
        elif operation == 'rename':
            if native.get('kind') not in {
                'partition_key', 'clustering'
            }:
                raise CassandraClientError(
                    'Cassandra can rename only primary-key columns'
                )
            source = f'ALTER TABLE {target} RENAME {_quoted(column)} TO ' \
                f'{_quoted(draft.get("new_name"))}'
        elif operation == 'drop':
            source = f'ALTER TABLE {target} DROP {_quoted(column)}'
        else:
            raise CassandraClientError('column operation is unavailable')
        return {'source': source, 'parameters': (),
                'values_parameterized': True}

    def _index_statement(self, operation, draft, native):
        if operation == 'drop':
            keyspace = self._target_name(native, 'keyspace_name', 'keyspace')
            name = self._target_name(native, 'index_name', 'index')
            return {'source': f'DROP INDEX {_quoted(keyspace)}.'
                    f'{_quoted(name)}',
                    'parameters': (), 'values_parameterized': True}
        keyspace = _identifier(draft.get('keyspace'), 'keyspace')
        table = _identifier(draft.get('table'), 'table')
        name = _identifier(draft.get('name'), 'index')
        target = _identifier(draft.get('target'), 'index target')
        if not re.fullmatch(
            r'(?:KEYS|VALUES|ENTRIES|FULL)\([A-Za-z][A-Za-z0-9_]*\)|'
            r'[A-Za-z][A-Za-z0-9_]*', target, re.IGNORECASE
        ):
            raise CassandraClientError('index target is invalid')
        kind = draft.get('index_kind', 'sai')
        prefix = 'CREATE INDEX'
        suffix = ''
        if kind in {'sai', 'custom'}:
            prefix = 'CREATE CUSTOM INDEX'
            class_name = (
                'StorageAttachedIndex' if kind == 'sai'
                else _identifier(draft.get('index_class'), 'index class')
            )
            if not _JAVA_NAME.fullmatch(class_name):
                raise CassandraClientError('custom index class is invalid')
            suffix = f' USING {_literal(class_name)}'
        elif kind != 'secondary':
            raise CassandraClientError('index kind is invalid')
        options = draft.get('options', {})
        if options:
            suffix += ' WITH OPTIONS = ' + _literal(
                _mapping(options, 'options')
            )
        return {
            'source': f'{prefix} IF NOT EXISTS {_quoted(name)} ON '
            f'{_quoted(keyspace)}.{_quoted(table)} ({target}){suffix}',
            'parameters': (), 'values_parameterized': False,
        }

    def _view_statement(self, operation, draft, native):
        if operation == 'drop':
            keyspace = self._target_name(native, 'keyspace_name', 'keyspace')
            name = self._target_name(native, 'view_name', 'view')
            return {'source': f'DROP MATERIALIZED VIEW '
                    f'{_quoted(keyspace)}.{_quoted(name)}',
                    'parameters': (), 'values_parameterized': True}
        keyspace = _identifier(draft.get('keyspace'), 'keyspace')
        name = _identifier(draft.get('name'), 'view')
        base = _identifier(draft.get('base_table'), 'base table')
        selected = self._name_array(
            draft.get('select_columns'), 'selected columns', allow_star=True
        )
        not_null = self._name_array(
            draft.get('not_null_columns'), 'non-null columns'
        )
        partition = self._name_array(
            draft.get('partition_keys'), 'partition keys'
        )
        clustering = self._name_array(
            draft.get('clustering_keys', []), 'clustering keys'
        )
        if not selected or not not_null or not partition:
            raise CassandraClientError(
                'materialized view columns and keys must not be empty'
            )
        projection = '*' if selected == ['*'] else ', '.join(
            _quoted(item) for item in selected
        )
        partition_source = (
            _quoted(partition[0]) if len(partition) == 1 else
            '(' + ', '.join(_quoted(item) for item in partition) + ')'
        )
        primary = partition_source
        if clustering:
            primary += ', ' + ', '.join(_quoted(item) for item in clustering)
        source = (
            f'CREATE MATERIALIZED VIEW IF NOT EXISTS {_quoted(keyspace)}.'
            f'{_quoted(name)} AS SELECT {projection} FROM '
            f'{_quoted(keyspace)}.{_quoted(base)} WHERE ' + ' AND '.join(
                f'{_quoted(item)} IS NOT NULL' for item in not_null
            ) + f' PRIMARY KEY ({primary})'
        )
        if draft.get('options'):
            source += self._with_options(draft['options'])
        return {'source': source, 'parameters': (),
                'values_parameterized': False}

    def _type_statement(self, operation, draft, native):
        keyspace = (
            _identifier(draft.get('keyspace'), 'keyspace')
            if operation == 'create'
            else self._target_name(native, 'keyspace_name', 'keyspace')
        )
        name = (
            _identifier(draft.get('name'), 'type')
            if operation == 'create'
            else self._target_name(native, 'type_name', 'type')
        )
        target = f'{_quoted(keyspace)}.{_quoted(name)}'
        if operation == 'drop':
            source = f'DROP TYPE {target}'
        else:
            fields = [_mapping(item, 'type field')
                      for item in draft.get('fields', [])]
            if not fields:
                raise CassandraClientError('type fields must not be empty')
            definitions = ', '.join(
                f'{_quoted(item.get("name"))} '
                f'{_type_expression(item.get("type"))}'
                for item in fields
            )
            if operation == 'create':
                source = f'CREATE TYPE IF NOT EXISTS {target} ({definitions})'
            elif operation == 'alter' and len(fields) == 1:
                source = f'ALTER TYPE {target} ADD {definitions}'
            else:
                raise CassandraClientError(
                    'type alter adds exactly one field per operation'
                )
        return {'source': source, 'parameters': (),
                'values_parameterized': True}

    def _function_statement(self, operation, draft, native):
        if operation == 'drop':
            keyspace = self._target_name(native, 'keyspace_name', 'keyspace')
            name = self._target_name(native, 'function_name', 'function')
            args = native.get('argument_types', [])
            return {'source': f'DROP FUNCTION {_quoted(keyspace)}.'
                    f'{_quoted(name)}('
                    f'{", ".join(_type_expression(item) for item in args)})',
                    'parameters': (), 'values_parameterized': True}
        keyspace = _identifier(draft.get('keyspace'), 'keyspace')
        name = _identifier(draft.get('name'), 'function')
        arguments = [_mapping(item, 'function argument')
                     for item in draft.get('arguments', [])]
        args = ', '.join(
            f'{_quoted(item.get("name"))} '
            f'{_type_expression(item.get("type"))}'
            for item in arguments
        )
        language = _identifier(draft.get('language'), 'function language')
        if not _SIMPLE_NAME.fullmatch(language):
            raise CassandraClientError('function language is invalid')
        body = draft.get('body')
        if not isinstance(body, str) or not body or '$$' in body:
            raise CassandraClientError('function body is invalid')
        null_mode = (
            'CALLED ON NULL INPUT' if draft.get('called_on_null_input')
            else 'RETURNS NULL ON NULL INPUT'
        )
        return {
            'source': f'CREATE OR REPLACE FUNCTION {_quoted(keyspace)}.'
            f'{_quoted(name)}({args}) {null_mode} RETURNS '
            f'{_type_expression(draft.get("return_type"))} LANGUAGE '
            f'{language} AS $$' + body + '$$',
            'parameters': (), 'values_parameterized': False,
        }

    def _aggregate_statement(self, operation, draft, native):
        if operation == 'drop':
            keyspace = self._target_name(native, 'keyspace_name', 'keyspace')
            name = self._target_name(native, 'aggregate_name', 'aggregate')
            args = native.get('argument_types', [])
            return {'source': f'DROP AGGREGATE {_quoted(keyspace)}.'
                    f'{_quoted(name)}('
                    f'{", ".join(_type_expression(item) for item in args)})',
                    'parameters': (), 'values_parameterized': True}
        keyspace = _identifier(draft.get('keyspace'), 'keyspace')
        name = _identifier(draft.get('name'), 'aggregate')
        arguments = draft.get('argument_types', [])
        if not isinstance(arguments, list):
            raise CassandraClientError('aggregate argument types are invalid')
        state_function = _identifier(
            draft.get('state_function'), 'state function'
        )
        source = (
            f'CREATE AGGREGATE IF NOT EXISTS {_quoted(keyspace)}.'
            f'{_quoted(name)}('
            f'{", ".join(_type_expression(item) for item in arguments)}) '
            f'SFUNC {_quoted(state_function)} STYPE '
            f'{_type_expression(draft.get("state_type"))}'
        )
        if draft.get('final_function'):
            source += ' FINALFUNC ' + _quoted(draft['final_function'])
        if 'initial_condition' in draft:
            source += ' INITCOND ' + _literal(_json_literal(
                draft['initial_condition'], 'aggregate initial condition'
            ))
        return {'source': source, 'parameters': (),
                'values_parameterized': False}

    def _security_statements(self, kind, operation, draft, native, preview):
        if kind == 'role' and operation in {'create', 'alter'}:
            name = (
                _identifier(draft.get('name'), 'role')
                if operation == 'create'
                else self._target_name(native, 'role', 'role')
            )
            clauses = [
                f'LOGIN = {_literal(bool(draft.get("login", False)))}',
                f'SUPERUSER = '
                f'{_literal(bool(draft.get("superuser", False)))}',
            ]
            reference = draft.get('password_credential_reference')
            if reference:
                _identifier(reference, 'role password reference')
                clauses.append('PASSWORD = <CDEADMIN_PASSWORD>')
            options = draft.get('options', {})
            if options:
                clauses.append(
                    'OPTIONS = ' + _literal(_mapping(options, 'role options'))
                )
            statement = {
                'source': f'{operation.upper()} ROLE '
                f'{"IF NOT EXISTS " if operation == "create" else ""}'
                f'{_quoted(name)} WITH ' + ' AND '.join(clauses),
                'parameters': (), 'values_parameterized': False,
            }
            if reference:
                statement['sensitive_password_reference'] = reference
                if preview:
                    statement['source'] = statement['source'].replace(
                        '<CDEADMIN_PASSWORD>', '<secret-reference>'
                    )
            return [statement]
        if kind == 'role' and operation == 'drop':
            return [{'source':
                     'DROP ROLE ' + _quoted(self._target_name(
                         native, 'role', 'role'
                     )),
                    'parameters': (), 'values_parameterized': True}]
        privileges = draft.get('privileges', [])
        if not isinstance(privileges, list) or not privileges:
            raise CassandraClientError(
                'permissions or roles must not be empty'
            )
        principal = _identifier(draft.get('principal'), 'role')
        statements = []
        if kind == 'role':
            for role in privileges:
                statements.append({
                    'source': f'{operation.upper()} '
                    f'{_quoted(role, "granted role")} '
                    f'{"TO" if operation == "grant" else "FROM"} '
                    f'{_quoted(principal)}',
                    'parameters': (), 'values_parameterized': True,
                })
            return statements
        resource = _mapping(
            draft.get('resource', {'kind': 'all-keyspaces'}),
            'permission resource',
        )
        resource_source = self._permission_resource(resource)
        for permission in privileges:
            permission = _identifier(permission, 'permission').upper()
            if permission not in {
                'ALL', 'ALTER', 'AUTHORIZE', 'CREATE', 'DESCRIBE',
                'DROP', 'EXECUTE', 'MASK', 'MODIFY', 'SELECT', 'UNMASK',
            }:
                raise CassandraClientError('permission is invalid')
            statements.append({
                'source': f'{operation.upper()} {permission} ON '
                f'{resource_source} '
                f'{"TO" if operation == "grant" else "FROM"} '
                f'{_quoted(principal)}',
                'parameters': (), 'values_parameterized': True,
            })
        return statements

    @staticmethod
    def _permission_resource(resource):
        kind = resource.get('kind')
        if kind == 'all-keyspaces':
            return 'ALL KEYSPACES'
        if kind == 'keyspace':
            return 'KEYSPACE ' + _quoted(resource.get('keyspace'))
        if kind == 'table':
            return f'TABLE {_quoted(resource.get("keyspace"))}.' \
                f'{_quoted(resource.get("table"))}'
        if kind == 'all-roles':
            return 'ALL ROLES'
        if kind == 'role':
            return 'ROLE ' + _quoted(resource.get('role'))
        if kind == 'all-functions':
            return 'ALL FUNCTIONS IN KEYSPACE ' + _quoted(
                resource.get('keyspace')
            )
        raise CassandraClientError('permission resource is invalid')

    def _inspect_statements(self, kind, native):
        if kind == 'role':
            return [{
                'source': 'LIST ROLES OF ' + _quoted(
                    native.get('role'), 'role'
                ),
                'parameters': (),
                'values_parameterized': False,
            }]
        mapping = {
            'cluster': ('SELECT * FROM system.local', ()),
            'datacenter': ('SELECT * FROM system.peers_v2 '
                           'WHERE data_center = %s ALLOW FILTERING',
                           (native.get('data_center'),)),
            'node': ('SELECT * FROM system.peers_v2 WHERE host_id = %s '
                     'ALLOW FILTERING', (native.get('host_id'),)),
            'keyspace': ('SELECT * FROM system_schema.keyspaces '
                         'WHERE keyspace_name = %s',
                         (native.get('keyspace_name'),)),
            'replication': ('SELECT * FROM system_schema.keyspaces '
                            'WHERE keyspace_name = %s',
                            (native.get('keyspace_name'),)),
            'table': ('SELECT * FROM system_schema.tables WHERE '
                      'keyspace_name = %s AND table_name = %s',
                      (native.get('keyspace_name'), native.get('table_name'))),
            'column': ('SELECT * FROM system_schema.columns WHERE '
                       'keyspace_name = %s AND table_name = %s AND '
                       'column_name = %s',
                       (native.get('keyspace_name'), native.get('table_name'),
                        native.get('column_name'))),
            'index': ('SELECT * FROM system_schema.indexes WHERE '
                      'keyspace_name = %s AND table_name = %s AND '
                      'index_name = %s',
                      (native.get('keyspace_name'), native.get('table_name'),
                       native.get('index_name'))),
            'materialized-view': ('SELECT * FROM system_schema.views WHERE '
                                  'keyspace_name = %s AND view_name = %s',
                                  (native.get('keyspace_name'),
                                   native.get('view_name'))),
            'user-defined-type': ('SELECT * FROM system_schema.types WHERE '
                                  'keyspace_name = %s AND type_name = %s',
                                  (native.get('keyspace_name'),
                                   native.get('type_name'))),
            'function': ('SELECT * FROM system_schema.functions WHERE '
                         'keyspace_name = %s AND function_name = %s',
                         (native.get('keyspace_name'),
                          native.get('function_name'))),
            'aggregate': ('SELECT * FROM system_schema.aggregates WHERE '
                          'keyspace_name = %s AND aggregate_name = %s',
                          (native.get('keyspace_name'),
                           native.get('aggregate_name'))),
            'permission': ('LIST ALL PERMISSIONS', ()),
            'query': ('SELECT * FROM system_views.clients', ()),
            'tracing-session': ('SELECT * FROM system_traces.sessions WHERE '
                                'session_id = %s',
                                (native.get('session_id'),)),
        }
        try:
            source, parameters = mapping[kind]
        except KeyError as exc:
            raise CassandraClientError(
                'Cassandra inspection operation is unavailable'
            ) from exc
        return [{'source': source, 'parameters': parameters,
                 'values_parameterized': bool(parameters)}]

    @staticmethod
    def _native_target(target):
        if not isinstance(target, Mapping):
            raise CassandraClientError('target resource is invalid')
        extension = target.get('extensions', {}).get('cassandra', {})
        native = extension.get('native')
        if not isinstance(native, Mapping):
            native = target.get('native')
        if not isinstance(native, Mapping):
            raise CassandraClientError(
                'target lacks Cassandra native identity'
            )
        return copy.deepcopy(dict(native))

    @staticmethod
    def _table_target(native):
        return (
            _identifier(native.get('keyspace_name'), 'keyspace'),
            _identifier(native.get('table_name'), 'table'),
        )

    @staticmethod
    def _name_array(value, label, allow_star=False):
        if not isinstance(value, list):
            raise CassandraClientError(f'{label} must be an array')
        result = []
        for item in value:
            if allow_star and item == '*':
                result.append(item)
            else:
                result.append(_identifier(item, label))
        if len(result) != len(set(result)):
            raise CassandraClientError(f'{label} must be unique')
        return result

    @staticmethod
    def _with_options(options):
        options = _mapping(options, 'CQL options')
        if not options:
            return ''
        clauses = []
        for key, value in options.items():
            key = _identifier(key, 'option name')
            if not _SIMPLE_NAME.fullmatch(key):
                raise CassandraClientError('CQL option name is invalid')
            clauses.append(f'{key} = {_literal(value)}')
        return ' WITH ' + ' AND '.join(clauses)

    @staticmethod
    def _using_options(options):
        clauses = []
        if options.get('ttl') is not None:
            clauses.append('TTL ' + str(_bounded_int(
                options['ttl'], None, 0, 630720000, 'TTL'
            )))
        if options.get('timestamp') is not None:
            timestamp = options['timestamp']
            if isinstance(timestamp, bool) or not isinstance(timestamp, int):
                raise CassandraClientError('timestamp must be an integer')
            clauses.append('TIMESTAMP ' + str(timestamp))
        return ' USING ' + ' AND '.join(clauses) if clauses else ''

    @staticmethod
    def _route_fingerprint(route):
        admitted = {
            key: value for key, value in route.items()
            if key not in {'route_id', 'tool_workspace'}
        }
        return hashlib.sha256(json.dumps(
            admitted, sort_keys=True, default=str
        ).encode('utf-8')).hexdigest()

    def _consume_identity(self, draft, keyspace, table, route, consume=True):
        selector = _mapping(draft.get('selector'), 'row selector')
        token = _identifier(
            selector.get('identity_token'), 'row identity token'
        )
        identity = self._row_identities.get(token)
        if identity is None:
            raise CassandraClientError('row identity token is stale')
        if time.monotonic() - identity.issued_at > IDENTITY_TTL_SECONDS:
            raise CassandraClientError('row identity token has expired')
        if (identity.keyspace, identity.table) != (keyspace, table):
            raise CassandraClientError(
                'row identity belongs to another table'
            )
        if identity.route_fingerprint != self._route_fingerprint(
            self._route({'route': route})
        ):
            raise CassandraClientError(
                'row identity belongs to another endpoint route'
            )
        if consume:
            self._row_identities.pop(token, None)
        return identity

    def read_admin_rows(self, request):
        target = self._native_target(request.get('target_resource'))
        keyspace, table = self._table_target(target)
        limit = _bounded_int(
            request.get('limit'), 200, 1, MAX_PAGE_SIZE, 'row limit'
        )
        route = self._route({'route': request.get('_provider_route')})
        fingerprint = self._route_fingerprint(route)
        continuation = request.get('continuation')
        paging_state = None
        if continuation is not None:
            cursor_id = _identifier(continuation, 'row continuation')
            cursor = self._admin_cursors.pop(cursor_id, None)
            if cursor is None or (
                cursor.route_fingerprint != fingerprint or
                cursor.keyspace != keyspace or cursor.table != table
            ):
                raise CassandraClientError(
                    'Cassandra row continuation is unavailable'
                )
            source = cursor.statement
            parameters = cursor.parameters
            paging_state = cursor.paging_state
            limit = cursor.limit
        else:
            filters = _mapping(request.get('filter', {}), 'row filter')
            key_columns = self._primary_key_columns(target)
            unknown = set(filters).difference(key_columns)
            if unknown:
                raise CassandraClientError(
                    'row filters are limited to primary-key columns'
                )
            parameters = tuple(filters.values())
            predicate = ''
            if filters:
                predicate = ' WHERE ' + ' AND '.join(
                    f'{_quoted(name)} = %s' for name in filters
                )
            source = f'SELECT * FROM {_quoted(keyspace)}.{_quoted(table)}' \
                f'{predicate}'
        cluster = session = None
        try:
            cluster, session, _actual_route = self._connect({'route': route})
            statement = self._statement(source, route, limit)
            result = session.execute(
                statement, parameters, paging_state=paging_state
            )
            raw_rows = list(getattr(result, 'current_rows', ()) or ())[:limit]
            rows = [self._row_mapping(item) for item in raw_rows]
            columns = self._columns_for_table(target, rows)
            key_columns = self._primary_key_columns(target)
            output_rows = []
            for row in rows:
                token = None
                if key_columns and all(name in row for name in key_columns):
                    token = str(uuid.uuid4())
                    while len(self._row_identities) >= MAX_IDENTITIES:
                        self._row_identities.pop(next(iter(
                            self._row_identities
                        )), None)
                    self._row_identities[token] = _RowIdentity(
                        fingerprint, keyspace, table, key_columns,
                        tuple(row[name] for name in key_columns),
                        copy.deepcopy(row), time.monotonic(),
                    )
                output_rows.append({
                    'values': self._json_value(row),
                    'identity_token': token,
                })
            has_more = bool(getattr(result, 'has_more_pages', False))
            next_cursor = None
            if has_more:
                if len(self._admin_cursors) >= MAX_ACTIVE_CURSORS:
                    raise CassandraClientError(
                        'Cassandra active row cursor limit is reached'
                    )
                state = getattr(result, 'paging_state', None)
                if state:
                    next_cursor = str(uuid.uuid4())
                    self._admin_cursors[next_cursor] = _AdminCursor(
                        fingerprint, keyspace, table, source, parameters,
                        bytes(state), limit,
                    )
            return {
                'schema': 'cdeadmin.visual-admin.wide-column-page.v1',
                'resource_kind': 'table',
                'columns': columns,
                'rows': output_rows,
                'editable': bool(key_columns),
                'identity_policy': (
                    'provider-partition-and-clustering-key-identity'
                    if key_columns else 'read-only-no-primary-key-metadata'
                ),
                'limit': limit,
                'complete': not has_more,
                'continuation': next_cursor,
                'transaction_finality_interpreted_by_common_code': False,
            }
        finally:
            if cluster is not None:
                self._close_native(cluster, session)

    def cancel_admin_cursor(self, request):
        token = _identifier(request.get('continuation'), 'row continuation')
        removed = self._admin_cursors.pop(token, None) is not None
        return {
            'cancel_request_accepted': removed,
            'outcome': 'provider-cursor-forgotten' if removed else 'absent',
        }

    @staticmethod
    def _primary_key_columns(target):
        columns = target.get('columns', [])
        if isinstance(columns, list):
            ordered = sorted(
                (item for item in columns if isinstance(item, Mapping) and
                 item.get('kind') in {'partition_key', 'clustering'}),
                key=lambda item: (
                    0 if item.get('kind') == 'partition_key' else 1,
                    item.get('position', 0),
                ),
            )
            if ordered:
                return tuple(str(item['column_name']) for item in ordered)
        keys = target.get('primary_key', [])
        return tuple(str(item) for item in keys) if isinstance(keys, list) \
            else ()

    def _columns_for_table(self, target, rows):
        metadata = target.get('columns', [])
        if isinstance(metadata, list) and metadata:
            keys = set(self._primary_key_columns(target))
            return [{
                'name': str(item.get('column_name')),
                'native_type': str(item.get('type', 'cql-value')),
                'kind': item.get('kind'),
                'position': item.get('position'),
                'key': item.get('column_name') in keys,
                'editable': True,
            } for item in sorted(
                metadata, key=lambda value: (
                    0 if value.get('kind') == 'partition_key' else 1,
                    0 if value.get('kind') == 'clustering' else 1,
                    value.get('position', 0),
                    value.get('column_name', ''),
                )
            )]
        names = list(rows[0]) if rows else []
        return [{
            'name': str(name), 'native_type': 'cql-value',
            'key': False, 'editable': False,
        } for name in names]

    @staticmethod
    def _workspace_path(workspace, value, label):
        root = Path(_identifier(workspace, 'tool workspace')).resolve(
            strict=False
        )
        if not root.is_absolute() or root in {Path('/'), Path.home()}:
            raise CassandraClientError('tool workspace grant is unsafe')
        candidate = Path(_identifier(value, label))
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError:
            raise CassandraClientError(
                f'{label} escapes the tool workspace'
            ) from None
        if candidate == root:
            raise CassandraClientError(f'{label} must identify a child path')
        return root, candidate

    @staticmethod
    def _tool_grant(executable, route):
        port = route['jmx_port'] if executable == 'nodetool' else route['port']
        return ProviderToolGrant(
            executable,
            _identifier(route.get('tool_workspace'), 'tool workspace'),
            _identifier(route.get('host'), 'endpoint host'),
            port,
            secret_environment_names=(
                ('CQLSH_PYTHON',) if executable == 'cqlsh' else ()
            ),
        )

    def _tool_secret(self, route, callback):
        reference = route.get('credential_reference_id')
        if reference is None:
            return callback(None)
        if not callable(self._secret_acquirer):
            raise CassandraClientError(
                'Cassandra tool credential binding is unavailable'
            )
        lease = self._secret_acquirer(
            _identifier(reference, 'credential reference'),
            _identifier(
                route.get('principal_reference'), 'principal reference'
            ),
            'provider_tool', 'database_password',
        )
        with lease:
            return lease.use(
                lambda value: callback(bytes(value).decode('utf-8'))
            )

    def _apply_tool(self, payload, route):
        if payload['operation_id'] == 'inspect':
            return {
                'available_executables': self._tool_runner.available(),
                'driver_observation_only': True,
                'transaction_finality_interpreted_by_common_code': False,
            }
        draft = payload['draft']
        action = _identifier(draft.get('action'), 'tool action')
        arguments = _mapping(draft.get('arguments', {}), 'tool arguments')
        kind = payload['resource_kind']
        executable, command = self._tool_command(kind, action, arguments,
                                                 route)
        grant = self._tool_grant(executable, route)
        if executable == 'sstableloader' and route.get(
            'credential_reference_id'
        ):
            raise CassandraClientError(
                'authenticated sstableloader is refused because Cassandra '
                'accepts its password only as a visible process argument'
            )

        def invoke(password):
            secret_config = None
            secret_argument = '--config={path}'
            secret_suffix = '.conf'
            secret_environment = None
            if executable == 'cqlsh':
                sections = []
                if password is not None:
                    username = _identifier(
                        route.get('username'), 'Cassandra username'
                    )
                    sections.append(
                        '[authentication]\n'
                        f'username = {username}\npassword = {password}\n'
                    )
                if route['tls_mode'] == 'self-signed':
                    sections.append('[ssl]\nvalidate = false\n')
                elif route['tls_mode'] == 'system-ca':
                    ca_file = route.get('tls_ca_file')
                    if not ca_file:
                        raise CassandraClientError(
                            'Cassandra system-CA cqlsh requires an explicit '
                            'TLS CA file; plaintext downgrade is refused'
                        )
                    sections.append(
                        f'[ssl]\ncertfile = {ca_file}\nvalidate = true\n'
                    )
                if sections:
                    secret_config = ''.join(sections).encode('utf-8')
                secret_argument = '--cqlshrc={path}'
                secret_suffix = '.cqlshrc'
                python = shutil.which('python3.11')
                if python is not None:
                    secret_environment = {'CQLSH_PYTHON': python}
            return self._tool_runner.run(
                grant, command, secret_config=secret_config,
                secret_argument=secret_argument,
                secret_suffix=secret_suffix,
                redact_values=(str(password or ''),),
                secret_environment=secret_environment,
            )

        try:
            result = (
                self._tool_secret(route, invoke)
                if executable == 'cqlsh'
                else invoke(None)
            )
        except ProviderToolError as exc:
            raise CassandraClientError(str(exc)) from None
        if result['return_code'] != 0:
            raise CassandraClientError(
                f'Cassandra {executable} exited with a failure: '
                f'{result["stderr"][-500:]}'
            )
        result.update({'operation': action, 'acknowledged': True})
        return {
            'tool_result': result,
            'driver_observation_only': True,
            'transaction_finality_interpreted_by_common_code': False,
        }

    def _tool_command(self, kind, action, arguments, route):
        keyspace = arguments.get('keyspace')
        table = arguments.get('table')
        if keyspace is not None:
            keyspace = _identifier(keyspace, 'keyspace')
        if table is not None:
            table = _identifier(table, 'table')
        if kind == 'repair' and action == 'repair':
            return 'nodetool', [
                '-h', route['host'], '-p', str(route['jmx_port']), 'repair',
            ] + ([keyspace] if keyspace else [])
        if kind == 'compaction' and action in {'compact', 'cleanup'}:
            return 'nodetool', [
                '-h', route['host'], '-p', str(route['jmx_port']), action,
            ] + ([keyspace] if keyspace else [])
        if kind == 'snapshot' and action in {'snapshot', 'clearsnapshot'}:
            name = _identifier(arguments.get('name'), 'snapshot name')
            flag = '-t' if action == 'snapshot' else '-t'
            return 'nodetool', [
                '-h', route['host'], '-p', str(route['jmx_port']),
                action, flag, name,
            ] + (
                [keyspace] if keyspace else []
            )
        if kind == 'backup' and action in {'snapshot', 'refresh'}:
            if action == 'snapshot':
                name = _identifier(arguments.get('name'), 'snapshot name')
                return 'nodetool', [
                    '-h', route['host'], '-p', str(route['jmx_port']),
                    'snapshot', '-t', name,
                ] + (
                    [keyspace] if keyspace else []
                )
            if not keyspace or not table:
                raise CassandraClientError(
                    'refresh requires keyspace and table'
                )
            _root, path = self._workspace_path(
                route.get('tool_workspace'), arguments.get('path'),
                'SSTable path',
            )
            return 'nodetool', [
                '-h', route['host'], '-p', str(route['jmx_port']),
                'refresh', keyspace, table, str(path),
            ]
        if kind == 'restore' and action == 'sstableloader':
            _root, path = self._workspace_path(
                route.get('tool_workspace'), arguments.get('path'),
                'SSTable path',
            )
            return 'sstableloader', [
                '-d', _identifier(route.get('host'), 'host'), str(path)
            ]
        if kind == 'shell' and action == 'file':
            _root, path = self._workspace_path(
                route.get('tool_workspace'), arguments.get('path'),
                'CQL file',
            )
            command = [
                _identifier(route.get('host'), 'host'),
                str(route.get('port', 9042)), '-f', str(path),
            ]
            if route['tls_mode'] != 'disabled':
                command.insert(0, '--ssl')
            return 'cqlsh', command
        raise CassandraClientError('Cassandra tool action is unavailable')

    def close(self):
        for handle in list(self._sessions):
            try:
                handle.close()
            except Exception:
                pass
        self._sessions.clear()
        self._results.clear()
        self._row_identities.clear()
        self._admin_cursors.clear()
