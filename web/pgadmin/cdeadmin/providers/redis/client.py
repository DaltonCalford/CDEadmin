##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Bounded RESP3 boundary for Redis 8.6 and newer stable releases.

Redis command, transaction, replication, persistence and cluster semantics
remain owned by the connected server and the qualified redis-py adapter.  The
client never retries a mutation after an uncertain response and never exposes
Redis persistence observations as transaction finality for another engine.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib
import json
import re
import shlex
import ssl
import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from pgadmin.cdeadmin.sdk import (
    PilotProviderError,
    ProviderToolGrant,
    ProviderToolRunner,
)


QUALIFIED_DRIVER_VERSION = '6.4.0'
MAX_COMMAND_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_RESULT_RECORDS = 10000
MAX_PAGE_SIZE = 500
MAX_RECURSION_DEPTH = 24
MAX_ACTIVE_CURSORS = 128
IDENTITY_TTL_SECONDS = 600

_TEXT_NAME = re.compile(r'^[^\x00-\x1f\x7f]{1,1024}$')
_CLIENT_NAME = re.compile(r'^[!-~]{1,1024}$')
_READ_ONLY_COMMANDS = frozenset({
    'ACL|CAT', 'ACL|DRYRUN', 'ACL|GENPASS', 'ACL|GETUSER', 'ACL|LIST',
    'ACL|LOG', 'ACL|USERS', 'ACL|WHOAMI', 'BITCOUNT', 'BITFIELD_RO',
    'BITPOS', 'COMMAND', 'DBSIZE', 'DUMP', 'ECHO', 'EXISTS', 'EXPIRETIME',
    'GEODIST', 'GEOHASH', 'GEOPOS', 'GEOSEARCH', 'GET', 'GETBIT', 'HELLO',
    'GETRANGE', 'HEXISTS', 'HGET', 'HGETALL', 'HKEYS', 'HLEN', 'HMGET',
    'HRANDFIELD', 'HSCAN', 'HSTRLEN', 'HTTL', 'HVALS', 'INFO', 'KEYS',
    'LASTSAVE', 'LATENCY|DOCTOR', 'LATENCY|GRAPH', 'LATENCY|HISTORY',
    'LATENCY|LATEST', 'LINDEX', 'LLEN', 'LPOS', 'LRANGE', 'MEMORY|DOCTOR',
    'MEMORY|MALLOC-STATS', 'MEMORY|STATS', 'MEMORY|USAGE', 'MODULE|LIST',
    'OBJECT|ENCODING', 'OBJECT|FREQ', 'OBJECT|IDLETIME', 'OBJECT|REFCOUNT',
    'PFCOUNT', 'PING', 'PTTL', 'PUBSUB|CHANNELS', 'PUBSUB|NUMPAT',
    'PUBSUB|NUMSUB', 'RANDOMKEY', 'ROLE', 'SCAN', 'SCARD', 'SCRIPT|EXISTS',
    'SDIFF', 'SINTER', 'SISMEMBER', 'SMEMBERS', 'SMISMEMBER', 'SRANDMEMBER',
    'SSCAN', 'STRLEN', 'SUBSTR', 'TIME', 'TTL', 'TYPE', 'VINFO', 'VCARD',
    'VDIM', 'VEMB', 'VGETATTR', 'VRANDMEMBER', 'VSIM', 'XRANGE',
    'XREAD', 'XREVRANGE', 'XLEN', 'XPENDING', 'XINFO|CONSUMERS',
    'XINFO|GROUPS', 'XINFO|STREAM', 'ZCARD', 'ZCOUNT', 'ZDIFF', 'ZINTER',
    'ZLEXCOUNT', 'ZMSCORE', 'ZRANDMEMBER', 'ZRANGE', 'ZRANGEBYLEX',
    'ZRANGEBYSCORE', 'ZRANK', 'ZREVRANGE', 'ZREVRANGEBYLEX',
    'ZREVRANGEBYSCORE', 'ZREVRANK', 'ZSCAN', 'ZSCORE',
})


class RedisClientError(PilotProviderError):
    """A Redis dependency, route, or native operation failed safely."""


class RedisDependencyError(RedisClientError):
    """The selected redis-py dependency is unavailable or unqualified."""


class RedisUnknownOutcomeError(RedisClientError):
    """A mutation response was lost and must not be replayed automatically."""

    outcome_unknown = True
    retryable = False


@dataclass
class _RedisSession:
    client: object
    route: dict[str, Any]
    topology: object | None = None
    closed: bool = False

    def close(self):
        if self.closed:
            return
        close = getattr(self.client, 'close', None)
        if callable(close):
            close()
        close = getattr(self.topology, 'close', None)
        if callable(close):
            close()
        self.closed = True


@dataclass
class _RedisResult:
    command: tuple[bytes, ...]
    value: object = None
    complete: bool = True
    cancelled: bool = False
    outcome: str = 'observed'


@dataclass(frozen=True)
class _RowIdentity:
    route_fingerprint: str
    key: bytes
    data_type: str
    selector: object
    original_value: object
    original_digest: str
    issued_at: float


@dataclass(frozen=True)
class _AdminCursor:
    route_fingerprint: str
    key: bytes | None
    data_type: str
    native_cursor: object
    limit: int


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RedisClientError(f'{label} must be an object')
    return copy.deepcopy(dict(value))


def _text(value: object, label: str, maximum=1024) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RedisClientError(f'{label} must not be empty')
    value = value.strip()
    if len(value) > maximum or not _TEXT_NAME.fullmatch(value):
        raise RedisClientError(f'{label} contains forbidden characters')
    return value


def _bounded_int(value, default, minimum, maximum, label):
    if value is None:
        if default is None:
            raise RedisClientError(f'{label} is required')
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise RedisClientError(f'{label} must be an integer')
    if not minimum <= value <= maximum:
        raise RedisClientError(
            f'{label} must be between {minimum} and {maximum}'
        )
    return value


def _binary(value: object, label='value') -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode('utf-8')
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value).encode('ascii')
    if isinstance(value, Mapping) and set(value) == {'$binary'}:
        encoded = value['$binary']
        if not isinstance(encoded, str):
            raise RedisClientError(f'{label} binary payload must be text')
        try:
            return base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise RedisClientError(
                f'{label} binary payload is not valid base64'
            ) from exc
    raise RedisClientError(
        f'{label} must be text, numeric, bytes, or a $binary object'
    )


class RedisClient:
    """Synchronous, bounded Redis 8.6+ adapter over redis-py."""

    ROUTE_KEYS = frozenset({
        'route_id', 'host', 'port', 'contact_points', 'topology_mode',
        'sentinel_service', 'database', 'username', 'user',
        'principal_reference', 'credential_reference_id', 'tls_mode',
        'tls_ca_file', 'tls_certificate_file', 'tls_key_file',
        'connect_timeout', 'socket_timeout', 'health_check_interval',
        'client_name', 'readonly', 'tool_workspace', 'auth_mode',
        'credential_kinds', 'credential_references', 'sentinel_username',
        'sentinel_min_other_sentinels', 'sentinel_read_replica',
        'cluster_require_full_coverage', 'cluster_dynamic_startup_nodes',
        'cluster_reinitialize_steps', 'cluster_error_retry_attempts',
        'unix_socket_path', 'tls_ca_path', 'tls_check_hostname',
        'tls_min_version', 'tls_ciphers', 'tls_validate_ocsp',
        'max_connections', 'socket_keepalive',
    })
    DATA_KINDS = frozenset({
        'key', 'string', 'hash', 'list', 'set', 'sorted-set', 'stream',
        'geospatial', 'bitmap', 'hyperloglog', 'vector-set', 'ttl',
    })
    TOOL_KINDS = frozenset({'backup', 'restore', 'import', 'export', 'shell'})
    ADMIN_OPERATIONS = {
        'deployment': frozenset({'inspect', 'execute'}),
        'node': frozenset({'inspect', 'alter', 'execute'}),
        'replica': frozenset({'inspect', 'alter', 'execute'}),
        'sentinel': frozenset({'inspect', 'alter', 'execute'}),
        'cluster-slot': frozenset({'inspect', 'alter', 'execute'}),
        'database': frozenset({'inspect', 'drop'}),
        'key': frozenset({'inspect', 'insert', 'update', 'delete'}),
        'string': frozenset({
            'inspect', 'create', 'alter', 'rename', 'insert', 'update',
            'delete', 'drop',
        }),
        'hash': frozenset({
            'inspect', 'create', 'alter', 'rename', 'insert', 'update',
            'delete', 'drop',
        }),
        'list': frozenset({
            'inspect', 'create', 'alter', 'rename', 'insert', 'update',
            'delete', 'drop',
        }),
        'set': frozenset({
            'inspect', 'create', 'alter', 'rename', 'insert', 'update',
            'delete', 'drop',
        }),
        'sorted-set': frozenset({
            'inspect', 'create', 'alter', 'rename', 'insert', 'update',
            'delete', 'drop',
        }),
        'stream': frozenset({
            'inspect', 'create', 'alter', 'rename', 'insert', 'delete', 'drop',
        }),
        'geospatial': frozenset({
            'inspect', 'create', 'alter', 'rename', 'insert', 'update',
            'delete', 'drop',
        }),
        'bitmap': frozenset({
            'inspect', 'create', 'alter', 'rename', 'insert', 'update',
            'delete', 'drop',
        }),
        'hyperloglog': frozenset({
            'inspect', 'create', 'alter', 'rename', 'insert', 'drop',
        }),
        'vector-set': frozenset({
            'inspect', 'create', 'alter', 'rename', 'insert', 'update',
            'delete', 'drop',
        }),
        'consumer-group': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'consumer': frozenset({'inspect', 'drop'}),
        'pubsub-channel': frozenset({'inspect', 'execute'}),
        'function-library': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'script': frozenset({'inspect', 'create', 'drop', 'execute'}),
        'acl-user': frozenset({
            'inspect', 'create', 'alter', 'grant', 'revoke', 'drop',
        }),
        'module': frozenset({'inspect'}),
        'ttl': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'transaction': frozenset({'inspect', 'execute'}),
        'pipeline': frozenset({'inspect', 'execute'}),
        'persistence': frozenset({'inspect', 'alter', 'execute'}),
        'configuration': frozenset({'inspect', 'alter', 'execute'}),
        'client': frozenset({'inspect', 'alter', 'execute'}),
        'slow-log': frozenset({'inspect', 'execute'}),
        'latency': frozenset({'inspect', 'execute'}),
        'backup': frozenset({'inspect', 'execute'}),
        'restore': frozenset({'inspect', 'execute'}),
        'import': frozenset({'inspect', 'execute'}),
        'export': frozenset({'inspect', 'execute'}),
        'shell': frozenset({'inspect', 'execute'}),
    }

    def __init__(self, secret_acquirer=None, module=None):
        try:
            if module is None:
                root = importlib.import_module('redis')
                sentinel = importlib.import_module('redis.sentinel')
                cluster = importlib.import_module('redis.cluster')
                exceptions = importlib.import_module('redis.exceptions')
                module = SimpleNamespace(
                    __version__=getattr(root, '__version__', None),
                    Redis=root.Redis,
                    RedisCluster=cluster.RedisCluster,
                    ClusterNode=cluster.ClusterNode,
                    Sentinel=sentinel.Sentinel,
                    TimeoutError=exceptions.TimeoutError,
                    ConnectionError=exceptions.ConnectionError,
                    WatchError=exceptions.WatchError,
                )
        except (ImportError, ModuleNotFoundError) as exc:
            raise RedisDependencyError(
                'Redis client dependency redis is unavailable'
            ) from exc
        observed = getattr(module, '__version__', None)
        if observed is not None and str(observed) != QUALIFIED_DRIVER_VERSION:
            raise RedisDependencyError(
                'redis-py version is not the qualified 6.4.0'
            )
        for name in ('Redis', 'RedisCluster', 'ClusterNode', 'Sentinel'):
            if not callable(getattr(module, name, None)):
                raise RedisDependencyError(
                    f'redis-py lacks approved {name}'
                )
        self.module = module
        self._secret_acquirer = secret_acquirer
        self._sessions: list[_RedisSession] = []
        self._results: list[_RedisResult] = []
        self._row_identities: dict[str, _RowIdentity] = {}
        self._admin_cursors: dict[str, _AdminCursor] = {}
        self._tool_runner = ProviderToolRunner({
            'redis-cli': 'redis-cli',
            'redis-check-rdb': 'redis-check-rdb',
            'redis-check-aof': 'redis-check-aof',
        })

    @classmethod
    def _route(cls, request):
        route = _mapping(
            request.get('route', request.get('_provider_route')),
            'Redis route',
        )
        unknown = sorted(set(route).difference(cls.ROUTE_KEYS))
        if unknown:
            raise RedisClientError(
                'Redis route contains unknown fields: ' + ', '.join(unknown)
            )
        if route.get('user') is not None:
            if route.get('username') not in {None, route['user']}:
                raise RedisClientError('Redis route user aliases disagree')
            route['username'] = route.pop('user')
        route['host'] = _text(route.get('host'), 'Redis host')
        route['port'] = _bounded_int(
            route.get('port'), 6379, 1, 65535, 'Redis port'
        )
        mode = route.get('topology_mode', 'standalone')
        if mode not in {'standalone', 'sentinel', 'cluster'}:
            raise RedisClientError('Redis topology mode is invalid')
        route['topology_mode'] = mode
        points = route.get('contact_points', [])
        if isinstance(points, str):
            points = [
                item.strip() for item in points.split(',') if item.strip()
            ]
        if not isinstance(points, list):
            raise RedisClientError('Redis contact points must be an array')
        route['contact_points'] = [
            cls._contact_point(item, route['port']) for item in points
        ]
        if mode == 'sentinel':
            route['sentinel_service'] = _text(
                route.get('sentinel_service'), 'Sentinel service name'
            )
        elif route.get('sentinel_service') is not None:
            raise RedisClientError(
                'Sentinel service is valid only in Sentinel mode'
            )
        route['database'] = _bounded_int(
            route.get('database'), 0, 0, 65535, 'Redis database'
        )
        if mode == 'cluster' and route['database'] != 0:
            raise RedisClientError('Redis Cluster supports database 0 only')
        for name in ('username', 'client_name'):
            if route.get(name) is not None:
                route[name] = _text(route[name], f'Redis {name}')
        auth_mode = route.get(
            'auth_mode', 'acl' if route.get('username') else 'none'
        )
        if auth_mode not in {'none', 'password', 'acl'}:
            raise RedisClientError('Redis authentication mode is invalid')
        route['auth_mode'] = auth_mode
        references = route.get('credential_references') or {}
        if not isinstance(references, Mapping):
            raise RedisClientError(
                'Redis credential references must be an object'
            )
        reference = route.get('credential_reference_id')
        if reference is not None:
            references.setdefault('database_password', _text(
                reference, 'credential reference'
            ))
        route['credential_references'] = dict(references)
        if auth_mode in {'password', 'acl'} and (
            'database_password' not in references
        ):
            raise RedisClientError(
                'Redis authentication requires a credential reference'
            )
        if auth_mode == 'acl' and not route.get('username'):
            raise RedisClientError(
                'Redis ACL authentication requires a username'
            )
        if auth_mode != 'acl':
            route.pop('username', None)
        if references:
            route['principal_reference'] = _text(
                route.get('principal_reference'), 'principal reference'
            )
        tls_mode = route.get('tls_mode', 'disabled')
        if tls_mode not in {'disabled', 'system-ca', 'self-signed'}:
            raise RedisClientError('Redis TLS mode is invalid')
        route['tls_mode'] = tls_mode
        for name in (
            'tls_ca_file', 'tls_ca_path', 'tls_certificate_file',
            'tls_key_file', 'unix_socket_path',
        ):
            value = route.get(name)
            if value is None:
                continue
            path = Path(_text(value, f'Redis {name}', 4096))
            if not path.is_absolute():
                raise RedisClientError(f'Redis {name} must be absolute')
            route[name] = str(path.resolve(strict=False))
        if tls_mode == 'disabled' and any(
            route.get(name) for name in (
                'tls_ca_file', 'tls_certificate_file', 'tls_key_file'
            )
        ):
            raise RedisClientError('Redis TLS files require TLS mode')
        if bool(route.get('tls_certificate_file')) != bool(
            route.get('tls_key_file')
        ):
            raise RedisClientError(
                'Redis client certificate and key must be supplied together'
            )
        route['connect_timeout'] = _bounded_int(
            route.get('connect_timeout'), 10, 1, 120, 'connect timeout'
        )
        route['socket_timeout'] = _bounded_int(
            route.get('socket_timeout'), 30, 1, 600, 'socket timeout'
        )
        route['health_check_interval'] = _bounded_int(
            route.get('health_check_interval'), 30, 0, 300,
            'health-check interval'
        )
        route['client_name'] = route.get('client_name', 'CDEadmin')
        if not _CLIENT_NAME.fullmatch(route['client_name']):
            raise RedisClientError(
                'Redis client_name must use printable ASCII without spaces'
            )
        route['readonly'] = bool(route.get('readonly', False))
        for name, default, minimum, maximum, label in (
            ('sentinel_min_other_sentinels', 0, 0, 1000,
             'minimum other Sentinels'),
            ('cluster_reinitialize_steps', 5, 1, 1000,
             'cluster reinitialize steps'),
            ('cluster_error_retry_attempts', 3, 0, 100,
             'cluster retry attempts'),
            ('max_connections', 100, 1, 100000,
             'maximum connections'),
        ):
            route[name] = _bounded_int(
                route.get(name), default, minimum, maximum, label
            )
        for name, default in (
            ('sentinel_read_replica', False),
            ('cluster_require_full_coverage', True),
            ('cluster_dynamic_startup_nodes', True),
            ('socket_keepalive', True),
            ('tls_check_hostname', True),
            ('tls_validate_ocsp', False),
        ):
            if route.get(name, default) not in {True, False}:
                raise RedisClientError(f'Redis {name} must be true or false')
            route[name] = route.get(name, default)
        route['tls_min_version'] = route.get('tls_min_version', 'TLSv1_2')
        if route['tls_min_version'] not in {'TLSv1_2', 'TLSv1_3'}:
            raise RedisClientError('Redis minimum TLS version is invalid')
        if mode != 'sentinel' and (
            route.get('sentinel_username') or
            'sentinel_password' in references
        ):
            raise RedisClientError(
                'Redis Sentinel credentials require Sentinel topology'
            )
        return route

    @staticmethod
    def _contact_point(value, default_port):
        if isinstance(value, str):
            host, separator, port = value.rpartition(':')
            if separator and host and port.isdigit():
                return (_text(host, 'Redis contact host'), _bounded_int(
                    int(port), default_port, 1, 65535, 'Redis contact port'
                ))
            return (_text(value, 'Redis contact host'), default_port)
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return (
                _text(value[0], 'Redis contact host'),
                _bounded_int(value[1], default_port, 1, 65535,
                             'Redis contact port'),
            )
        raise RedisClientError('Redis contact point must be host[:port]')

    def _connection_options(self, route, credentials):
        retry_namespace = getattr(self.module, 'retry', None)
        backoff_namespace = getattr(self.module, 'backoff', None)
        if retry_namespace is None:
            retry_namespace = importlib.import_module('redis.retry')
        if backoff_namespace is None:
            backoff_namespace = importlib.import_module('redis.backoff')
        if retry_namespace is None or backoff_namespace is None:
            raise RedisDependencyError(
                'redis-py does not expose its retry policy controls'
            )
        options = {
            'username': route.get('username'),
            'password': credentials.get('database_password'),
            'db': route['database'],
            'protocol': 3,
            'decode_responses': False,
            'socket_connect_timeout': float(route['connect_timeout']),
            'socket_timeout': float(route['socket_timeout']),
            'health_check_interval': route['health_check_interval'],
            'client_name': route['client_name'],
            'max_connections': route['max_connections'],
            'socket_keepalive': route['socket_keepalive'],
            # redis-py 6.x otherwise retries connection/timeout failures by
            # default.  That is unsafe for mutations whose first execution
            # may already have reached the server.
            'retry': retry_namespace.Retry(
                backoff_namespace.NoBackoff(), 0
            ),
            'retry_on_timeout': False,
        }
        if route['tls_mode'] != 'disabled':
            options.update({
                'ssl': True,
                'ssl_cert_reqs': (
                    'required' if route['tls_mode'] == 'system-ca' else 'none'
                ),
                'ssl_ca_certs': route.get('tls_ca_file'),
                'ssl_ca_path': route.get('tls_ca_path'),
                'ssl_certfile': route.get('tls_certificate_file'),
                'ssl_keyfile': route.get('tls_key_file'),
                'ssl_password': credentials.get(
                    'tls_private_key_password'
                ),
                'ssl_check_hostname': route['tls_check_hostname'],
                'ssl_min_version': getattr(
                    ssl.TLSVersion, route['tls_min_version']
                ),
                'ssl_ciphers': route.get('tls_ciphers'),
                'ssl_validate_ocsp': route['tls_validate_ocsp'],
            })
        return {key: value for key, value in options.items()
                if value is not None}

    def _connect(self, request):
        route = self._route(request)

        def connect(credentials=None):
            credentials = credentials or {}
            options = self._connection_options(route, credentials)
            mode = route['topology_mode']
            if mode == 'standalone':
                if route.get('unix_socket_path'):
                    client = self.module.Redis(
                        unix_socket_path=route['unix_socket_path'], **options
                    )
                else:
                    client = self.module.Redis(
                        host=route['host'], port=route['port'], **options
                    )
                return client, None
            if mode == 'cluster':
                points = [(route['host'], route['port']),
                          *route['contact_points']]
                startup = [
                    self.module.ClusterNode(host, port)
                    for host, port in points
                ]
                cluster_options = dict(options)
                cluster_options.pop('db', None)
                client = self.module.RedisCluster(
                    startup_nodes=startup,
                    read_from_replicas=route['readonly'],
                    require_full_coverage=(
                        route['cluster_require_full_coverage']
                    ),
                    dynamic_startup_nodes=(
                        route['cluster_dynamic_startup_nodes']
                    ),
                    reinitialize_steps=route['cluster_reinitialize_steps'],
                    cluster_error_retry_attempts=(
                        route['cluster_error_retry_attempts']
                    ),
                    **cluster_options,
                )
                return client, None
            sentinels = [(route['host'], route['port']),
                         *route['contact_points']]
            sentinel_options = dict(options)
            sentinel_options.pop('db', None)
            # redis-py 6.4.0's Sentinel command response callbacks require
            # RESP2 for Redis 8.x Sentinel replies.  The discovered data
            # connection remains RESP3 through ``options`` below.
            sentinel_options['protocol'] = 2
            sentinel_options['username'] = route.get('sentinel_username')
            sentinel_options['password'] = credentials.get(
                'sentinel_password'
            )
            sentinel_options = {
                key: value for key, value in sentinel_options.items()
                if value is not None
            }
            topology = self.module.Sentinel(
                sentinels,
                min_other_sentinels=route['sentinel_min_other_sentinels'],
                sentinel_kwargs=sentinel_options,
            )
            method = (
                topology.slave_for if route['sentinel_read_replica']
                else topology.master_for
            )
            client = method(route['sentinel_service'], **options)
            return client, topology

        references = route.get('credential_references', {})
        if not references:
            client, topology = connect()
        else:
            if not callable(self._secret_acquirer):
                raise RedisClientError(
                    'Redis credential binding is unavailable'
                )
            credentials = {}
            with ExitStack() as stack:
                for kind, reference in sorted(references.items()):
                    if kind not in {
                        'database_password', 'sentinel_password',
                        'tls_private_key_password',
                    }:
                        raise RedisClientError(
                            'Redis credential kind is unsupported'
                        )
                    lease = stack.enter_context(self._secret_acquirer(
                        _text(reference, 'credential reference'),
                        route['principal_reference'], 'connect', kind,
                    ))
                    credentials[kind] = lease.use(
                        lambda value: bytes(value).decode('utf-8')
                    )
                client, topology = connect(credentials)
        return client, topology, route

    def runtime_identity(self, request, handle=None):
        owned = handle is None
        client = None
        topology = None
        try:
            if handle is None:
                client, topology, route = self._connect(request)
            else:
                client, route = handle.client, handle.route
            identity_client = client
            if route['topology_mode'] == 'cluster':
                identity_client = client.get_redis_connection(
                    client.get_default_node()
                )
            server = identity_client.info('server')
            if not isinstance(server, Mapping):
                raise RedisClientError('Redis INFO server reply is invalid')
            version = str(server.get('redis_version') or '')
            if not version:
                raise RedisClientError('Redis runtime omitted its version')
            hello = identity_client.execute_command('HELLO', 3)
            return {
                'engine_id': 'redis',
                'version': version,
                'build_id': str(
                    server.get('run_id') or server.get('redis_build_id') or
                    server.get('redis_git_sha1') or 'redis-build-unknown'
                ),
                'protocol_id': 'resp',
                'native': {
                    'protocol_version': 3,
                    'redis_mode': server.get('redis_mode'),
                    'os': server.get('os'),
                    'arch_bits': server.get('arch_bits'),
                    'multiplexing_api': server.get('multiplexing_api'),
                    'topology_mode': route['topology_mode'],
                    'hello': self._json_value(hello),
                    'driver_version': QUALIFIED_DRIVER_VERSION,
                },
            }
        except RedisClientError:
            raise
        except Exception as exc:
            raise RedisClientError(
                f'Redis runtime identity failed ({type(exc).__name__})'
            ) from None
        finally:
            if owned:
                self._close_native(client, topology)

    def open_session(self, request):
        try:
            client, topology, route = self._connect(request)
            client.ping()
            handle = _RedisSession(client, route, topology)
            self._sessions.append(handle)
            return handle
        except RedisClientError:
            raise
        except Exception as exc:
            raise RedisClientError(
                f'Redis session open failed ({type(exc).__name__})'
            ) from None

    @staticmethod
    def describe_transaction(handle):
        return {
            'native_boundary': 'redis-resp3-command',
            'database': handle.route['database'],
            'topology_mode': handle.route['topology_mode'],
            'multi_exec_atomicity': 'redis-server-owned',
            'watch_abort_outcome': 'redis-server-and-driver-owned',
            'pipeline_is_transaction': False,
            'lost_mutation_response': 'unknown-no-automatic-replay',
            'common_finality_inference': False,
            'retry_decision_owned_by_common_code': False,
            'scratchbird_mga_authority_inferred': False,
        }

    @staticmethod
    def _command_key(arguments):
        words = []
        for item in arguments[:2]:
            try:
                words.append(item.decode('ascii').upper())
            except (AttributeError, UnicodeDecodeError):
                break
        if len(words) > 1 and words[0] in {
            'ACL', 'CLIENT', 'CLUSTER', 'CONFIG', 'FUNCTION', 'LATENCY',
            'MEMORY', 'MODULE', 'OBJECT', 'PUBSUB', 'SCRIPT', 'SLOWLOG',
            'XGROUP', 'XINFO',
        }:
            return '|'.join(words)
        return words[0] if words else ''

    @classmethod
    def _is_read_only(cls, arguments):
        return cls._command_key(arguments) in _READ_ONLY_COMMANDS

    @staticmethod
    def _source_arguments(request):
        source = request.get('source')
        if isinstance(source, str):
            if not source.strip():
                raise RedisClientError('Redis command must not be empty')
            if len(source.encode('utf-8')) > MAX_COMMAND_BYTES:
                raise RedisClientError(
                    'Redis command exceeds the safety limit'
                )
            try:
                values = shlex.split(source, posix=True)
            except ValueError as exc:
                raise RedisClientError(
                    'Redis command quoting is invalid'
                ) from exc
            arguments = [
                _binary(value, 'command argument') for value in values
            ]
        elif isinstance(source, (list, tuple)):
            arguments = [
                _binary(value, 'command argument') for value in source
            ]
        else:
            raise RedisClientError(
                'Redis source must be a command string or token array'
            )
        parameters = request.get('parameters', ())
        if parameters:
            if not isinstance(parameters, (list, tuple)):
                raise RedisClientError('Redis parameters must be an array')
            arguments.extend(
                _binary(value, 'parameter') for value in parameters
            )
        if not arguments or not arguments[0]:
            raise RedisClientError('Redis command must not be empty')
        if sum(len(item) for item in arguments) > MAX_COMMAND_BYTES:
            raise RedisClientError('Redis command exceeds the safety limit')
        arguments = tuple(arguments)
        RedisClient._validate_command_safety(arguments)
        return arguments

    @classmethod
    def _validate_command_safety(cls, arguments):
        words = []
        for item in arguments:
            try:
                words.append(item.decode('utf-8'))
            except UnicodeDecodeError:
                words.append('')
        upper = [item.upper() for item in words]
        if upper[0] == 'AUTH' or (
            upper[0] == 'HELLO' and 'AUTH' in upper[1:]
        ):
            raise RedisClientError(
                'Redis authentication must use a credential reference'
            )
        if upper[:2] == ['ACL', 'SETUSER'] and any(
            item.startswith(('>', '<', '#')) for item in words[3:]
        ):
            raise RedisClientError(
                'Redis ACL passwords must use a credential reference'
            )
        if upper[:2] == ['CONFIG', 'SET'] and len(upper) > 2 and (
            upper[2] in {'REQUIREPASS', 'MASTERAUTH', 'MASTERUSER'}
        ):
            raise RedisClientError(
                'Redis authentication settings are not admitted in commands'
            )
        if upper[0] == 'MIGRATE' and any(
            item in {'AUTH', 'AUTH2'} for item in upper[1:]
        ):
            raise RedisClientError(
                'Redis MIGRATE authentication must not contain inline secrets'
            )

    def execute(self, handle, request):
        arguments = self._source_arguments(request)
        read_only = self._is_read_only(arguments)
        try:
            executor = handle.client
            if (
                handle.route['topology_mode'] == 'cluster' and
                self._command_key(arguments) == 'HELLO'
            ):
                executor = handle.client.get_redis_connection(
                    handle.client.get_default_node()
                )
            value = executor.execute_command(*arguments)
            token = _RedisResult(arguments, value)
            self._results.append(token)
            return token
        except Exception as exc:
            uncertain = isinstance(exc, tuple(filter(None, (
                getattr(self.module, 'TimeoutError', None),
                getattr(self.module, 'ConnectionError', None),
            ))))
            if uncertain and not read_only:
                raise RedisUnknownOutcomeError(
                    'Redis mutation outcome is unknown; automatic replay is '
                    'forbidden and post-state validation is required'
                ) from None
            raise RedisClientError(
                f'Redis command failed ({type(exc).__name__})'
            ) from None

    @classmethod
    def _json_value(cls, value, depth=0):
        if depth > MAX_RECURSION_DEPTH:
            raise RedisClientError('Redis reply exceeds nesting limit')
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, bytes):
            try:
                text = value.decode('utf-8')
                if all(
                    char.isprintable() or char in '\r\n\t' for char in text
                ):
                    return text
            except UnicodeDecodeError:
                pass
            return {'$binary': base64.b64encode(value).decode('ascii')}
        if isinstance(value, Mapping):
            return {
                str(cls._json_value(key, depth + 1)):
                cls._json_value(item, depth + 1)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [cls._json_value(item, depth + 1) for item in value]
        return str(value)

    def describe_result(self, token):
        value = self._json_value(token.value)
        encoded = json.dumps(value, sort_keys=True).encode('utf-8')
        if len(encoded) > MAX_RESULT_BYTES:
            raise RedisClientError('Redis result exceeds the safety limit')
        command = self._command_key(token.command)
        return {
            'result_kind': 'key_value',
            'schema': {
                'columns': [
                    {'name': 'command', 'type': 'string'},
                    {'name': 'value', 'type': 'redis-value'},
                    {'name': 'outcome', 'type': 'string'},
                ]
            },
            'complete': token.complete,
            'payload': {'entries': [{
                'command': command,
                'value': value,
                'outcome': token.outcome,
            }]},
        }

    @staticmethod
    def cancel(token):
        if token.complete or token.cancelled:
            return False
        token.cancelled = True
        token.complete = True
        return False

    @staticmethod
    def _close_native(client, topology=None):
        for item in (client, topology):
            close = getattr(item, 'close', None)
            if callable(close):
                close()

    @staticmethod
    def _generation(*values):
        return hashlib.sha256(json.dumps(
            values, sort_keys=True, default=str
        ).encode('utf-8')).hexdigest()[:20]

    @classmethod
    def _resource(cls, kind, name, native, parent=None, generation=None):
        label = name if isinstance(name, str) else str(cls._json_value(name))
        identity = json.dumps(
            [kind, cls._json_value(native)], sort_keys=True, default=str
        ).encode('utf-8')
        resource_id = 'redis:' + kind + ':' + base64.urlsafe_b64encode(
            hashlib.sha256(identity).digest()[:18]
        ).decode('ascii').rstrip('=')
        path = ['redis']
        if parent:
            path.append(str(parent))
        path.extend([kind, label])
        return {
            'resource_id': resource_id,
            'resource_kind': kind,
            'display_name': label,
            'authority_path': path,
            'generation': generation or cls._generation(kind, native),
            'native': copy.deepcopy(native),
        }

    @staticmethod
    def _safe_call(default, callback):
        try:
            return callback()
        except Exception:
            return default

    @staticmethod
    def _decode_type(value):
        if isinstance(value, bytes):
            value = value.decode('ascii', errors='replace')
        aliases = {
            'zset': 'sorted-set',
            'vectorset': 'vector-set',
            'none': 'key',
        }
        return aliases.get(str(value), str(value))

    def list_resources(self, request):
        client = topology = None
        resources = []
        try:
            client, topology, route = self._connect(request)
            server = self._safe_call({}, lambda: client.info('server'))
            replication = self._safe_call(
                {}, lambda: client.info('replication')
            )
            persistence = self._safe_call(
                {}, lambda: client.info('persistence')
            )
            mode = route['topology_mode']
            generation = self._generation(server, replication, persistence)
            resources.append(self._resource(
                'deployment',
                f'Redis {server.get("redis_version", "unknown")}',
                {
                    'topology_mode': mode,
                    'redis_mode': server.get('redis_mode'),
                    'role': replication.get('role'),
                }, generation=generation,
            ))
            resources.append(self._resource(
                'node', f'{route["host"]}:{route["port"]}',
                {
                    'host': route['host'], 'port': route['port'],
                    'role': replication.get('role'),
                    'run_id': server.get('run_id'),
                }, parent='deployment', generation=generation,
            ))
            for index in range(int(replication.get('connected_slaves', 0))):
                row = replication.get(f'slave{index}', {})
                resources.append(self._resource(
                    'replica', f'{row.get("ip", "unknown")}:'
                    f'{row.get("port", "unknown")}',
                    self._json_value(row), parent='replication',
                    generation=generation,
                ))
            if mode == 'sentinel' and topology is not None:
                master = self._safe_call(
                    None,
                    lambda: topology.discover_master(
                        route['sentinel_service']
                    ),
                )
                replicas = self._safe_call(
                    [],
                    lambda: topology.discover_slaves(
                        route['sentinel_service']
                    ),
                )
                resources.append(self._resource(
                    'sentinel', route['sentinel_service'],
                    {'master': master, 'replicas': replicas},
                    parent='deployment', generation=generation,
                ))
            if mode == 'cluster':
                cluster_text = self._safe_call(
                    b'', lambda: client.execute_command('CLUSTER', 'NODES')
                )
                resources.extend(self._cluster_resources(
                    cluster_text, generation
                ))
            keyspace = self._safe_call({}, lambda: client.info('keyspace'))
            databases = sorted({route['database'], *(
                int(str(name)[2:]) for name in keyspace
                if str(name).startswith('db') and str(name)[2:].isdigit()
            )})
            for database in databases[:256]:
                resources.append(self._resource(
                    'database', f'db{database}',
                    {'database': database,
                     'statistics': self._json_value(
                         keyspace.get(f'db{database}', {})
                     )},
                    parent='deployment', generation=generation,
                ))
            requested_limit = _bounded_int(
                request.get('limit'), 200, 1, MAX_PAGE_SIZE,
                'resource limit',
            )
            _cursor, keys = client.scan(count=requested_limit)
            for key in list(keys)[:requested_limit]:
                data_type = self._decode_type(client.type(key))
                ttl = self._safe_call(-2, lambda key=key: client.pttl(key))
                native = {
                    'key': self._json_value(key),
                    'data_type': data_type,
                    'database': route['database'],
                    'ttl_ms': ttl,
                }
                key_resource = self._resource(
                    'key', str(self._json_value(key)), native,
                    parent=f'db{route["database"]}', generation=generation,
                )
                resources.append(key_resource)
                resources.append(self._resource(
                    data_type if data_type in self.DATA_KINDS else 'key',
                    str(self._json_value(key)), native,
                    parent=key_resource['resource_id'],
                    generation=generation,
                ))
                resources.append(self._resource(
                    'ttl', 'Expiry', native,
                    parent=key_resource['resource_id'],
                    generation=generation,
                ))
                if data_type == 'stream':
                    resources.extend(self._stream_group_resources(
                        client, key, key_resource['resource_id'], generation
                    ))
            resources.extend(self._operational_resources(
                client, generation
            ))
            return resources[:MAX_RESULT_RECORDS]
        except RedisClientError:
            raise
        except Exception as exc:
            raise RedisClientError(
                f'Redis resource discovery failed ({type(exc).__name__})'
            ) from None
        finally:
            self._close_native(client, topology)

    def _cluster_resources(self, value, generation):
        if isinstance(value, bytes):
            value = value.decode('utf-8', errors='replace')
        resources = []
        for line in str(value).splitlines()[:1000]:
            columns = line.split()
            if len(columns) < 8:
                continue
            node_id, address, flags = columns[0], columns[1], columns[2]
            node = {
                'node_id': node_id, 'address': address,
                'flags': flags.split(','), 'master_id': columns[3],
                'ping_sent': columns[4], 'pong_received': columns[5],
                'config_epoch': columns[6], 'link_state': columns[7],
            }
            resources.append(self._resource(
                'node', address, node, parent='cluster',
                generation=generation,
            ))
            for slot in columns[8:]:
                resources.append(self._resource(
                    'cluster-slot', slot,
                    {'slot_range': slot, 'node_id': node_id},
                    parent=node_id, generation=generation,
                ))
        return resources

    def _stream_group_resources(
        self, client, stream, parent, generation,
    ):
        groups = self._safe_call([], lambda: client.execute_command(
            'XINFO', 'GROUPS', stream
        ))
        resources = []
        for row in list(groups)[:1000]:
            if not isinstance(row, Mapping):
                continue
            name = row.get(b'name', row.get('name'))
            if name is None:
                continue
            native = {
                **self._json_value(row),
                'stream': self._json_value(stream),
                'group': self._json_value(name),
            }
            group = self._resource(
                'consumer-group', str(self._json_value(name)), native,
                parent=parent, generation=generation,
            )
            resources.append(group)
            consumers = self._safe_call(
                [], lambda name=name: client.execute_command(
                    'XINFO', 'CONSUMERS', stream, name
                )
            )
            for consumer_row in list(consumers)[:1000]:
                if not isinstance(consumer_row, Mapping):
                    continue
                consumer_name = consumer_row.get(
                    b'name', consumer_row.get('name')
                )
                if consumer_name is None:
                    continue
                consumer_native = {
                    **self._json_value(consumer_row),
                    'stream': self._json_value(stream),
                    'group': self._json_value(name),
                    'consumer': self._json_value(consumer_name),
                }
                resources.append(self._resource(
                    'consumer', str(self._json_value(consumer_name)),
                    consumer_native, parent=group['resource_id'],
                    generation=generation,
                ))
        return resources

    def _operational_resources(self, client, generation):
        resources = []
        factories = (
            ('persistence', 'Persistence', lambda: client.info('persistence')),
            ('configuration', 'Configuration',
             lambda: client.config_get('*')),
            ('slow-log', 'Slow log', lambda: client.slowlog_get(32)),
            ('latency', 'Latency',
             lambda: client.execute_command('LATENCY', 'LATEST')),
            ('module', 'Modules', lambda: client.module_list()),
            ('pubsub-channel', 'Pub/Sub channels',
             lambda: client.pubsub_channels()),
            ('client', 'Connected clients', lambda: client.client_list()),
            ('function-library', 'Function libraries',
             lambda: client.function_list()),
            ('acl-user', 'ACL users', lambda: client.acl_users()),
            ('transaction', 'MULTI/EXEC',
             lambda: {'native_outcome': True, 'automatic_retry': False}),
            ('pipeline', 'Pipeline',
             lambda: {'transactional': False, 'automatic_retry': False}),
        )
        for kind, name, factory in factories:
            value = self._safe_call(None, factory)
            if value is None:
                continue
            if kind == 'acl-user' and isinstance(value, (list, tuple)):
                for user in value[:1000]:
                    resources.append(self._resource(
                        kind, str(self._json_value(user)),
                        {'username': self._json_value(user)},
                        parent='security', generation=generation,
                    ))
                continue
            if kind == 'module' and isinstance(value, (list, tuple)):
                for module in value[:1000]:
                    if not isinstance(module, Mapping):
                        continue
                    module_name = module.get(b'name', module.get('name'))
                    if module_name is None:
                        continue
                    resources.append(self._resource(
                        kind, str(self._json_value(module_name)),
                        self._json_value(module), parent='modules',
                        generation=generation,
                    ))
                continue
            if kind == 'pubsub-channel' and isinstance(
                    value, (list, tuple)):
                for channel in value[:1000]:
                    resources.append(self._resource(
                        kind, str(self._json_value(channel)),
                        {'channel': self._json_value(channel)},
                        parent='pubsub', generation=generation,
                    ))
                resources.append(self._resource(
                    kind, 'Publish to channel', {'workspace': True},
                    parent='pubsub', generation=generation,
                ))
                continue
            resources.append(self._resource(
                kind, name, {'value': self._json_value(value)},
                parent='operations', generation=generation,
            ))
        return resources

    @staticmethod
    def _native_target(target):
        if not isinstance(target, Mapping):
            return {}
        extension = target.get('extensions', {}).get('redis', {})
        native = extension.get('native') if isinstance(extension, Mapping) \
            else None
        if not isinstance(native, Mapping):
            native = target.get('native', {})
        return (
            copy.deepcopy(dict(native))
            if isinstance(native, Mapping) else {}
        )

    def inspect_resource(self, request):
        client = topology = None
        target = self._native_target(request)
        kind = request.get('resource_kind') or target.get('data_type') or 'key'
        name = request.get('display_name') or target.get('key') or kind
        try:
            client, topology, route = self._connect(request)
            native = self._inspect_native(client, kind, target, route)
            return self._resource(
                kind, str(name), native,
                parent=f'db{route["database"]}',
            )
        finally:
            self._close_native(client, topology)

    def _inspect_native(self, client, kind, native, route):
        if kind in self.DATA_KINDS:
            key = self._target_key(native)
            data_type = self._decode_type(client.type(key))
            result = {
                'key': self._json_value(key), 'data_type': data_type,
                'database': route['database'],
                'ttl_ms': client.pttl(key),
                'encoding': self._json_value(self._safe_call(
                    None,
                    lambda: client.execute_command('OBJECT', 'ENCODING', key),
                )),
                'memory_bytes': self._safe_call(
                    None, lambda: client.memory_usage(key)
                ),
            }
            result['value'] = self._inspect_value(client, key, data_type)
            return result
        commands = {
            'deployment': ('INFO',), 'node': ('INFO', 'server'),
            'replica': ('INFO', 'replication'),
            'persistence': ('INFO', 'persistence'),
            'configuration': ('CONFIG', 'GET', '*'),
            'client': ('CLIENT', 'LIST'),
            'slow-log': ('SLOWLOG', 'GET', 128),
            'latency': ('LATENCY', 'LATEST'),
            'module': ('MODULE', 'LIST'),
            'pubsub-channel': ('PUBSUB', 'CHANNELS'),
            'function-library': ('FUNCTION', 'LIST'),
            'acl-user': ('ACL', 'GETUSER', native.get('username', 'default')),
            'cluster-slot': ('CLUSTER', 'SLOTS'),
        }
        command = commands.get(kind)
        value = client.execute_command(*command) if command else native
        if kind == 'acl-user' and isinstance(value, Mapping):
            value = {key: item for key, item in value.items()
                     if str(key).lower() != 'passwords'}
        return {'value': self._json_value(value)}

    def _inspect_value(self, client, key, data_type):
        commands = {
            'string': ('GET', key),
            'hash': ('HSCAN', key, 0, 'COUNT', MAX_PAGE_SIZE),
            'list': ('LRANGE', key, 0, MAX_PAGE_SIZE - 1),
            'set': ('SSCAN', key, 0, 'COUNT', MAX_PAGE_SIZE),
            'sorted-set': (
                'ZRANGE', key, 0, MAX_PAGE_SIZE - 1, 'WITHSCORES'
            ),
            'stream': ('XRANGE', key, '-', '+', 'COUNT', MAX_PAGE_SIZE),
            'vector-set': ('VRANGE', key, '-', '+', MAX_PAGE_SIZE),
        }
        command = commands.get(data_type)
        if command is None:
            return None
        return self._json_value(client.execute_command(*command))

    def describe_security(self, request):
        client = topology = None
        try:
            client, topology, _route = self._connect(request)
            who = self._safe_call('unknown', lambda: client.acl_whoami())
            users = self._safe_call([], lambda: client.acl_users())
            current = self._safe_call({}, lambda: client.acl_getuser(who))
            if isinstance(current, Mapping):
                current = {
                    key: value for key, value in current.items()
                    if str(key).lower() != 'passwords'
                }
            native = {
                'authorization_model': 'redis-acl',
                'current_user': self._json_value(who),
                'users': self._json_value(users),
                'current_rules': self._json_value(current),
            }
            return {
                'resource_id': 'redis:security:current',
                'display_name': 'Redis ACL authorization',
                'authority_path': ['redis', 'security', 'current'],
                'generation': self._generation(native),
                'native': native,
            }
        finally:
            self._close_native(client, topology)

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
        catalog['native_planner'] = 'redis-resp3-structured-planner'
        catalog['query_language'] = 'Redis RESP3 command tokens'
        catalog['key_identity'] = 'database-plus-binary-key'
        catalog['transaction_authority'] = 'redis-server-owned'
        catalog['native_outcomes_are_opaque'] = True
        catalog['automatic_mutation_retry'] = False
        catalog['experience_families'] = ['key_value']

        def declaration(*resource_kinds, status='supported', reason=None):
            return {
                'status': status,
                'resource_kinds': list(resource_kinds),
                'operation_obligations': {
                    kind: sorted(self.ADMIN_OPERATIONS[kind])
                    for kind in resource_kinds
                },
                'reason': reason or (
                    'Redis objects and operations are provider-owned '
                    'through the RESP3 data-structure surface.'
                ),
                'evidence': ['redis-8.6-plus-native-resp3-catalog'],
            }

        catalog['concept_declarations'] = {'key_value': {
            'key_browsing': declaration('key'),
            'data_type_editing': declaration(
                'string', 'hash', 'list', 'set', 'sorted-set',
                'geospatial', 'bitmap', 'hyperloglog', 'vector-set',
            ),
            'ttl_inspection': declaration('ttl'),
            'expiration_management': declaration('ttl'),
            'streams': declaration('stream'),
            'pubsub': declaration('pubsub-channel'),
            'consumer_groups': declaration('consumer-group', 'consumer'),
            'modules': declaration('module', status='read_only'),
            'acls': declaration('acl-user'),
            'replication': declaration('replica'),
            'sentinel_or_cluster_state': declaration(
                'sentinel', 'cluster-slot'
            ),
        }}
        for resource in catalog.get('objects', []):
            kind = resource['resource_kind']
            for operation in resource.get('operations', []):
                form = self._admin_form(kind, operation['operation_id'])
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
            return self._form('redis-inspect', 'Inspect', [])
        if kind in self.DATA_KINDS:
            if operation == 'rename':
                return self._form('redis-key-rename', 'Rename key', [
                    f('new_name', 'New key', required=True),
                ])
            if operation in {'create', 'insert'}:
                fields = [] if (
                    operation == 'insert' and kind not in {'key', 'string'}
                ) else [
                    f('name', 'Key', required=operation == 'create' or
                      kind in {'key', 'string'}),
                ]
                fields.extend([
                    f('values', 'Value, members, fields or elements',
                      'json', True),
                    f('options', 'Write options', 'json', False, default={}),
                ])
                return self._form(
                    f'redis-{kind}-{operation}',
                    f'{operation.title()} {kind}', fields,
                )
            if operation in {'alter', 'update'}:
                return self._form(
                    f'redis-{kind}-{operation}',
                    f'{operation.title()} {kind}', [
                        f('selector', 'Member, field, index or element',
                          'json', operation == 'update'),
                        f('changes', 'Changed value or options', 'json', True),
                    ],
                )
            if operation in {'delete', 'drop'}:
                fields = []
                if operation == 'delete' and kind not in {'key', 'string'}:
                    fields.append(f(
                        'selector', 'Members, fields, IDs or values',
                        'json', True,
                    ))
                fields.append(f(
                    'confirmation', 'Confirmation', required=True
                ))
                return self._form(
                    f'redis-{kind}-{operation}',
                    f'{operation.title()} {kind}', fields,
                )
        if kind == 'consumer-group' and operation in {'create', 'alter'}:
            return self._form(
                f'redis-consumer-group-{operation}',
                f'{operation.title()} consumer group', [
                    f('stream', 'Stream key', required=operation == 'create'),
                    f('name', 'Group name', required=operation == 'create'),
                    f('start_id', 'Start ID', required=False, default='$'),
                    f('make_stream', 'Create stream if absent', 'boolean',
                      False, default=False),
                    f('entries_read', 'Entries read', 'number', False),
                ],
            )
        if kind == 'pubsub-channel' and operation == 'execute':
            return self._form(
                'redis-pubsub-publish', 'Publish message', [
                    f('channel', 'Channel', required=True),
                    f('message', 'Message', required=True),
                ],
            )
        if kind == 'acl-user' and operation in {
            'create', 'alter', 'grant', 'revoke'
        }:
            return self._form(
                f'redis-acl-user-{operation}',
                f'{operation.title()} ACL user', [
                    f('name', 'ACL username',
                      required=operation == 'create'),
                    f('rules', 'ACL rule tokens', 'json', True, default=[]),
                    f('password_credential_reference',
                      'Password credential reference', 'secret-reference',
                      False, sensitive=True),
                ],
            )
        if kind == 'function-library' and operation in {'create', 'alter'}:
            return self._form(
                f'redis-function-{operation}',
                f'{operation.title()} function library', [
                    f('code', 'Function library source', 'code', True,
                      max_length=MAX_COMMAND_BYTES),
                    f('replace', 'Replace existing library', 'boolean', False,
                      default=operation == 'alter'),
                ],
            )
        if kind == 'ttl' and operation in {'create', 'alter'}:
            return self._form(
                f'redis-ttl-{operation}', f'{operation.title()} expiry', [
                    f('milliseconds', 'Expiry in milliseconds', 'number',
                      True),
                    f('condition', 'Condition', 'select', False,
                      default='none', options=[
                          {'value': 'none', 'label': 'Always'},
                          {'value': 'nx', 'label': 'Only without expiry'},
                          {'value': 'xx', 'label': 'Only with expiry'},
                          {'value': 'gt', 'label': 'Only if greater'},
                          {'value': 'lt', 'label': 'Only if less'},
                      ]),
                ],
            )
        if kind in {'transaction', 'pipeline'} and operation == 'execute':
            return self._form(
                f'redis-{kind}-execute', f'Run Redis {kind}', [
                    f('commands', 'Command token arrays', 'json', True,
                      default=[]),
                    f('watch_keys', 'WATCH keys', 'json', False, default=[]),
                    f('confirmation', 'Confirmation', required=True),
                ],
            )
        if operation == 'execute':
            return self._form(
                f'redis-{kind}-execute', f'Run {kind} operation', [
                    f('action', 'Action', required=True),
                    f('arguments', 'Arguments', 'json', False, default={}),
                    f('confirmation', 'Confirmation', required=False),
                ],
            )
        if operation == 'alter':
            return self._form(
                f'redis-{kind}-alter', f'Alter {kind}', [
                    f('changes', 'Requested changes', 'json', True),
                ],
            )
        return None

    def validate_admin_operation(self, request):
        errors = []
        try:
            self._compile_admin({
                'resource_kind': request['resource_kind'],
                'operation_id': request['operation_id'],
                'draft': copy.deepcopy(request.get('draft', {})),
                'native': self._native_target(
                    request.get('target_resource')
                ),
                '_provider_route': copy.deepcopy(
                    request.get('_provider_route')
                ),
            }, preview=True)
        except RedisClientError as exc:
            errors.append({
                'field_id': None,
                'code': 'redis_validation',
                'message': str(exc),
            })
        return {'errors': errors}

    @classmethod
    def _preview_arguments(cls, command):
        result = []
        for item in command:
            value = cls._json_value(item)
            if isinstance(value, str):
                result.append(value)
            else:
                result.append(value)
        return result

    def plan_admin_operation(self, request):
        native = self._native_target(request.get('target_resource'))
        payload = {
            'resource_kind': request['resource_kind'],
            'operation_id': request['operation_id'],
            'draft': copy.deepcopy(request.get('draft', {})),
            'native': native,
            '_provider_route': copy.deepcopy(request.get('_provider_route')),
        }
        compiled = self._compile_admin(payload, preview=True)
        warnings = []
        if compiled.get('transactional'):
            warnings.append(
                'MULTI/EXEC completion is a Redis server/driver observation; '
                'a lost response is not replayed.'
            )
        elif len(compiled.get('commands', [])) > 1:
            warnings.append(
                'The Redis commands form a non-transactional pipeline and '
                'may have a partial native outcome.'
            )
        return {
            'command_preview': {
                'driver': 'redis-py',
                'protocol': 'RESP3',
                'resource_kind': request['resource_kind'],
                'operation': request['operation_id'],
                'target': self._json_value(native),
                'commands': [
                    self._preview_arguments(command)
                    for command in compiled.get('commands', [])
                ],
                'transactional': bool(compiled.get('transactional')),
                'watch_keys': self._json_value(
                    compiled.get('watch_keys', [])
                ),
                'credential_references_redacted': True,
            },
            'provider_payload': payload,
            'warnings': warnings,
            'receipt': {
                'provider_owned': True,
                'automatic_mutation_retry': False,
                'common_transaction_finality_inference': False,
            },
        }

    def apply_admin_operation(self, request):
        payload = _mapping(request.get('provider_payload'), 'provider plan')
        route = self._route({'route': payload.pop('_provider_route', None)})
        payload['_provider_route'] = copy.deepcopy(route)
        kind = payload['resource_kind']
        if kind in self.TOOL_KINDS:
            return self._apply_tool(payload, route)
        client = topology = None
        try:
            client, topology, _actual_route = self._connect({'route': route})
            if kind == 'sentinel':
                return self._apply_sentinel(topology, payload, route)
            compiled = self._compile_admin(payload, preview=False)
            result = self._execute_compiled(client, compiled, payload, route)
            return {
                'provider_owned': True,
                'native_outcome': result['outcome'],
                'observations': self._json_value(result['values']),
                'commands_executed': len(compiled.get('commands', [])),
                'transactional': bool(compiled.get('transactional')),
                'automatic_retry_performed': False,
                'common_finality_inference': False,
                'post_state_validation_required': (
                    result['outcome'] == 'unknown'
                ),
            }
        finally:
            self._close_native(client, topology)

    def _execute_compiled(self, client, compiled, payload, route):
        commands = compiled.get('commands', [])
        if not commands:
            return {'outcome': 'observed', 'values': []}
        password_reference = compiled.get('password_reference')

        def execute(password=None):
            actual = copy.deepcopy(commands)
            if password is not None:
                marker = b'__CDEADMIN_ACL_PASSWORD__'
                actual = [
                    tuple(
                        b'>' + password if item == marker else item
                        for item in command
                    ) for command in actual
                ]
            watch_keys = compiled.get('watch_keys', [])
            row_identity = compiled.get('row_identity')
            if row_identity is not None and row_identity.key not in watch_keys:
                watch_keys = [*watch_keys, row_identity.key]
            transactional = bool(compiled.get('transactional'))
            if len(actual) == 1 and not transactional and not watch_keys:
                return [client.execute_command(*actual[0])]
            pipeline = client.pipeline(
                transaction=transactional or bool(watch_keys)
            )
            try:
                if watch_keys:
                    pipeline.watch(*watch_keys)
                    self._validate_row_identity(pipeline, compiled)
                    pipeline.multi()
                for command in actual:
                    pipeline.execute_command(*command)
                return pipeline.execute()
            finally:
                reset = getattr(pipeline, 'reset', None)
                if callable(reset):
                    reset()

        try:
            if password_reference is None:
                values = execute()
            else:
                if not callable(self._secret_acquirer):
                    raise RedisClientError(
                        'Redis ACL password binding is unavailable'
                    )
                principal = route.get('principal_reference')
                if principal is None:
                    raise RedisClientError(
                        'Redis ACL password change requires principal '
                        'reference'
                    )
                lease = self._secret_acquirer(
                    password_reference, principal, 'administer',
                    'database_password',
                )
                with lease:
                    values = lease.use(lambda value: execute(bytes(value)))
            return {'outcome': 'observed', 'values': values}
        except Exception as exc:
            watch_error = getattr(self.module, 'WatchError', None)
            if watch_error is not None and isinstance(exc, watch_error):
                return {'outcome': 'watch-aborted', 'values': []}
            uncertain_types = tuple(filter(None, (
                getattr(self.module, 'TimeoutError', None),
                getattr(self.module, 'ConnectionError', None),
            )))
            mutation = any(not self._is_read_only(command)
                           for command in commands)
            if (
                uncertain_types and isinstance(exc, uncertain_types) and
                mutation
            ):
                raise RedisUnknownOutcomeError(
                    'Redis administration outcome is unknown; automatic '
                    'replay is forbidden and post-state validation is required'
                ) from None
            raise RedisClientError(
                f'Redis administration failed ({type(exc).__name__})'
            ) from None

    def _compile_admin(self, payload, preview=False):
        kind = payload['resource_kind']
        operation = payload['operation_id']
        draft = _mapping(payload.get('draft', {}), 'Redis draft')
        native = _mapping(payload.get('native', {}), 'Redis native target')
        if not self.supports_admin_operation(kind, operation):
            raise RedisClientError(
                'Redis administration operation is unavailable'
            )
        if operation == 'inspect':
            return {'commands': []}
        if kind in self.DATA_KINDS:
            identity = None
            if operation in {'update', 'delete'}:
                identity = self._resolve_row_identity(
                    draft, native, kind, payload.get('_provider_route'),
                    consume=not preview,
                )
                if identity is not None:
                    draft['selector'] = copy.deepcopy(identity.selector)
                    draft['_identity_value'] = copy.deepcopy(
                        identity.original_value
                    )
                    draft['_identity_marker'] = identity.original_digest
                    draft['_identity_edit'] = True
            compiled = self._compile_data(
                kind, operation, draft, native,
                payload.get('_provider_route'), preview,
            )
            if identity is not None:
                compiled['row_identity'] = identity
            return compiled
        if kind == 'database' and operation == 'drop':
            return {'commands': [(b'FLUSHDB',)]}
        if kind in {'transaction', 'pipeline'} and operation == 'execute':
            commands = self._command_arrays(draft.get('commands'))
            watch = [_binary(value, 'WATCH key')
                     for value in self._array(draft.get('watch_keys', []),
                                              'WATCH keys')]
            if kind == 'pipeline' and watch:
                raise RedisClientError('WATCH is valid only for a transaction')
            return {
                'commands': commands,
                'transactional': kind == 'transaction',
                'watch_keys': watch,
            }
        if kind == 'consumer-group':
            return self._compile_consumer_group(operation, draft, native)
        if kind == 'consumer' and operation == 'drop':
            return {'commands': [(b'XGROUP', b'DELCONSUMER',
                                  self._native_bytes(native, 'stream'),
                                  self._native_bytes(native, 'group'),
                                  self._native_bytes(native, 'consumer'))]}
        if kind == 'pubsub-channel' and operation == 'execute':
            channel = native.get('channel', draft.get('channel'))
            return {'commands': [(b'PUBLISH',
                                  _binary(channel, 'channel'),
                                  _binary(draft.get('message'), 'message'))]}
        if kind == 'function-library':
            if operation in {'create', 'alter'}:
                command = [b'FUNCTION', b'LOAD']
                if draft.get('replace'):
                    command.append(b'REPLACE')
                command.append(_binary(draft.get('code'), 'function source'))
                return {'commands': [tuple(command)]}
            if operation == 'drop':
                return {'commands': [(b'FUNCTION', b'DELETE',
                                      self._native_bytes(native, 'library'))]}
        if kind == 'script':
            if operation == 'create':
                source = draft.get('definition') or draft.get('code')
                return {'commands': [(b'SCRIPT', b'LOAD',
                                      _binary(source, 'script source'))]}
            if operation == 'drop':
                return {'commands': [(b'SCRIPT', b'FLUSH', b'SYNC')]}
            if operation == 'execute':
                return self._compile_action(kind, draft, native)
        if kind == 'acl-user':
            return self._compile_acl(operation, draft, native, preview)
        if kind == 'configuration' and operation == 'alter':
            changes = _mapping(draft.get('changes'), 'configuration changes')
            commands = [(b'CONFIG', b'SET', _binary(key, 'setting'),
                         _binary(value, 'setting value'))
                        for key, value in changes.items()]
            if not commands:
                raise RedisClientError(
                    'configuration changes must not be empty'
                )
            return {'commands': commands}
        if kind == 'persistence' and operation == 'alter':
            changes = _mapping(draft.get('changes'), 'persistence changes')
            allowed = {'appendonly', 'appendfsync', 'save'}
            unknown = sorted(set(changes).difference(allowed))
            if not changes or unknown:
                raise RedisClientError(
                    'persistence changes accept appendonly, appendfsync, '
                    'and save only'
                )
            return {'commands': [
                (b'CONFIG', b'SET', _binary(name, 'persistence setting'),
                 _binary(value, 'persistence value'))
                for name, value in changes.items()
            ]}
        if kind == 'client' and operation == 'alter':
            changes = _mapping(draft.get('changes'), 'client changes')
            if set(changes) != {'name'}:
                raise RedisClientError(
                    'client alteration accepts only the current client name'
                )
            name = _text(changes['name'], 'client name')
            if not _CLIENT_NAME.fullmatch(name):
                raise RedisClientError(
                    'Redis client name must use printable ASCII without spaces'
                )
            return {'commands': [(b'CLIENT', b'SETNAME', _binary(
                name, 'client name'
            ))]}
        if operation == 'alter' and kind in {'node', 'replica'}:
            changes = _mapping(draft.get('changes'), 'replication changes')
            if changes.get('role') == 'primary':
                return {'commands': [(b'REPLICAOF', b'NO', b'ONE')]}
            return {'commands': [(b'REPLICAOF',
                                  _binary(changes.get('host'), 'primary host'),
                                  _binary(
                                      changes.get('port'), 'primary port'
                                  ))]}
        if operation == 'alter' and kind == 'cluster-slot':
            changes = _mapping(draft.get('changes'), 'cluster slot changes')
            return {'commands': [(
                b'CLUSTER', b'SETSLOT',
                _binary(changes.get('slot'), 'slot'),
                _binary(changes.get('state'), 'slot state'),
                *([_binary(changes['node_id'], 'node ID')]
                  if changes.get('node_id') is not None else []),
            )]}
        if operation == 'execute':
            return self._compile_action(kind, draft, native)
        raise RedisClientError('Redis administration operation is unavailable')

    @staticmethod
    def _array(value, label):
        if not isinstance(value, list):
            raise RedisClientError(f'{label} must be an array')
        return value

    @classmethod
    def _command_arrays(cls, value):
        commands = []
        for row in cls._array(value, 'commands'):
            if not isinstance(row, list) or not row:
                raise RedisClientError(
                    'each Redis command must be a non-empty token array'
                )
            command = tuple(_binary(item, 'command token') for item in row)
            cls._validate_command_safety(command)
            if sum(len(item) for item in command) > MAX_COMMAND_BYTES:
                raise RedisClientError(
                    'Redis command exceeds the safety limit'
                )
            commands.append(command)
        if not commands or len(commands) > 1000:
            raise RedisClientError(
                'commands must contain between 1 and 1000 rows'
            )
        return commands

    @staticmethod
    def _native_bytes(native, name):
        value = native.get(name)
        if value is None:
            raise RedisClientError(f'Redis target omitted {name}')
        return _binary(value, f'Redis target {name}')

    @classmethod
    def _target_key(cls, native, draft=None):
        value = native.get('key')
        if value is None and draft is not None:
            value = draft.get('name')
        if value is None:
            raise RedisClientError('Redis target omitted key')
        return _binary(value, 'Redis key')

    def _compile_data(self, kind, operation, draft, native, route, preview):
        key = self._target_key(native, draft)
        if operation == 'rename':
            return {'commands': [(b'RENAME', key,
                                  _binary(draft.get('new_name'), 'new key'))]}
        if operation == 'drop' or (
            operation == 'delete' and kind in {'key', 'string'}
        ):
            return {'commands': [(b'DEL', key)]}
        if kind == 'ttl':
            if operation in {'create', 'alter'}:
                value = _bounded_int(
                    draft.get('milliseconds'), None, 1, 2 ** 63 - 1,
                    'expiry milliseconds',
                )
                command = [b'PEXPIRE', key, str(value).encode('ascii')]
                condition = draft.get('condition', 'none')
                if condition != 'none':
                    command.append(str(condition).upper().encode('ascii'))
                return {'commands': [tuple(command)]}
            if operation == 'drop':
                return {'commands': [(b'PERSIST', key)]}
        if operation == 'alter':
            changes = _mapping(draft.get('changes'), 'key changes')
            if set(changes) != {'ttl_ms'}:
                raise RedisClientError(
                    'Redis container alteration accepts only ttl_ms'
                )
            ttl = _bounded_int(
                changes['ttl_ms'], None, 1, 2 ** 63 - 1,
                'expiry milliseconds',
            )
            return {'commands': [(b'PEXPIRE', key, str(ttl).encode('ascii'))]}
        values = draft.get('values')
        selector = draft.get('selector')
        changes = draft.get('changes')
        options = draft.get('options', {})
        identity_edit = bool(draft.get('_identity_edit'))
        if operation == 'update':
            values = changes
        if kind in {'key', 'string'}:
            item = _mapping(values, 'string value')
            command = [b'SET', key, _binary(item.get('value'), 'value')]
            self._append_set_options(command, options)
            return {'commands': [tuple(command)]}
        if kind == 'hash':
            if operation == 'update' and identity_edit:
                item = _mapping(values, 'hash field change')
                return {'commands': [(b'HSET', key, _binary(
                    selector, 'hash field'
                ), _binary(item.get('value'), 'hash value'))]}
            if operation in {'create', 'insert', 'update'}:
                fields = _mapping(values, 'hash fields')
                command = [b'HSET', key]
                for field, value in fields.items():
                    command.extend((_binary(field, 'hash field'),
                                    _binary(value, 'hash value')))
                return {'commands': [tuple(command)]}
            fields = (
                [selector] if identity_edit else
                self._selector_values(selector, 'fields')
            )
            return {'commands': [(b'HDEL', key, *[
                _binary(value, 'hash field') for value in fields
            ])]}
        if kind == 'list':
            if operation in {'create', 'insert'}:
                item = _mapping(values, 'list values')
                members = self._selector_values(item, 'values')
                side = str(item.get('side', 'right')).lower()
                if side not in {'left', 'right'}:
                    raise RedisClientError('list side must be left or right')
                command = b'LPUSH' if side == 'left' else b'RPUSH'
                return {'commands': [(command, key, *[
                    _binary(value, 'list value') for value in members
                ])]}
            if operation == 'update':
                item = (
                    {'index': selector} if identity_edit else
                    _mapping(selector, 'list selector')
                )
                index = _bounded_int(item.get('index'), None, -(2 ** 63),
                                     2 ** 63 - 1, 'list index')
                change = _mapping(values, 'list change')
                return {'commands': [(b'LSET', key, str(index).encode('ascii'),
                                      _binary(change.get('value'), 'value'))]}
            if identity_edit:
                index = _bounded_int(
                    selector, None, -(2 ** 63), 2 ** 63 - 1, 'list index'
                )
                expected = _binary(
                    draft.get('_identity_value'), 'original list value'
                )
                marker = (
                    b'__cdeadmin_deleted__' +
                    _binary(draft.get('_identity_marker'), 'identity marker')
                )
                source = (
                    b"local v=redis.call('LINDEX',KEYS[1],ARGV[1]);"
                    b"if v~=ARGV[2] then return redis.error_reply("
                    b"'CDEADMIN_STALE_ROW') end;"
                    b"redis.call('LSET',KEYS[1],ARGV[1],ARGV[3]);"
                    b"return redis.call('LREM',KEYS[1],1,ARGV[3])"
                )
                return {'commands': [(
                    b'EVAL', source, b'1', key,
                    str(index).encode('ascii'), expected, marker,
                )]}
            item = _mapping(selector, 'list selector')
            count = _bounded_int(item.get('count'), 0, -(2 ** 63),
                                 2 ** 63 - 1, 'list removal count')
            return {'commands': [(b'LREM', key, str(count).encode('ascii'),
                                  _binary(item.get('value'), 'value'))]}
        if kind == 'set':
            if operation == 'update' and identity_edit:
                change = _mapping(values, 'set member change')
                return {
                    'commands': [
                        (b'SREM', key, _binary(selector, 'old set member')),
                        (b'SADD', key, _binary(
                            change.get('value'), 'new set member'
                        )),
                    ],
                    'transactional': True,
                }
            command = b'SREM' if operation == 'delete' else b'SADD'
            members = (
                [selector] if operation == 'delete' and identity_edit else
                self._selector_values(
                    selector if operation == 'delete' else values, 'members'
                )
            )
            return {'commands': [(command, key, *[
                _binary(value, 'set member') for value in members
            ])]}
        if kind in {'sorted-set', 'geospatial'}:
            if operation == 'delete':
                members = (
                    [selector] if identity_edit else
                    self._selector_values(selector, 'members')
                )
                return {'commands': [(b'ZREM', key, *[
                    _binary(value, 'member') for value in members
                ])]}
            if operation == 'update' and identity_edit:
                change = _mapping(values, f'{kind} member change')
                if kind == 'geospatial':
                    position = _mapping(
                        change.get('value', change), 'geospatial position'
                    )
                    entries = [{
                        'member': selector,
                        'longitude': position.get('longitude'),
                        'latitude': position.get('latitude'),
                    }]
                else:
                    entries = [{
                        'member': selector, 'score': change.get('value')
                    }]
            else:
                entries = self._array(values, f'{kind} entries')
            command = [b'GEOADD' if kind == 'geospatial' else b'ZADD', key]
            for entry in entries:
                item = _mapping(entry, f'{kind} entry')
                if kind == 'geospatial':
                    command.extend((
                        _binary(item.get('longitude'), 'longitude'),
                        _binary(item.get('latitude'), 'latitude'),
                        _binary(item.get('member'), 'member'),
                    ))
                else:
                    command.extend((
                        _binary(item.get('score'), 'score'),
                        _binary(item.get('member'), 'member'),
                    ))
            return {'commands': [tuple(command)]}
        if kind == 'stream':
            if operation == 'delete':
                ids = (
                    [selector] if identity_edit else
                    self._selector_values(selector, 'ids')
                )
                return {'commands': [(b'XDEL', key, *[
                    _binary(value, 'stream ID') for value in ids
                ])]}
            item = _mapping(values, 'stream entry')
            fields = _mapping(item.get('fields'), 'stream fields')
            command = [b'XADD', key, _binary(item.get('id', '*'), 'stream ID')]
            for field, value in fields.items():
                command.extend((_binary(field, 'stream field'),
                                _binary(value, 'stream value')))
            return {'commands': [tuple(command)]}
        if kind == 'bitmap':
            if operation == 'update' and identity_edit:
                item = _mapping(values, 'bitmap change')
                return {'commands': [(b'SET', key, _binary(
                    item.get('value'), 'bitmap value'
                ))]}
            item = _mapping(values, 'bitmap value')
            offset = _bounded_int(item.get('offset'), None, 0,
                                  2 ** 32 - 1, 'bitmap offset')
            bit = _bounded_int(item.get('value'), None, 0, 1, 'bit value')
            return {'commands': [(b'SETBIT', key, str(offset).encode('ascii'),
                                  str(bit).encode('ascii'))]}
        if kind == 'hyperloglog':
            members = self._selector_values(values, 'values')
            return {'commands': [(b'PFADD', key, *[
                _binary(value, 'HyperLogLog value') for value in members
            ])]}
        if kind == 'vector-set':
            if operation == 'delete':
                members = (
                    [selector] if identity_edit else
                    self._selector_values(selector, 'elements')
                )
                return {'commands': [
                    (b'VREM', key, _binary(value, 'vector element'))
                    for value in members
                ]}
            if operation == 'update' and identity_edit:
                change = _mapping(values, 'vector change')
                vector = change.get('vector', change.get('value'))
                entries = [{
                    **change,
                    'vector': vector,
                    'element': selector,
                }]
            else:
                entries = self._array(values, 'vector entries')
            commands = []
            for entry in entries:
                item = _mapping(entry, 'vector entry')
                vector = self._array(item.get('vector'), 'vector components')
                command = [b'VADD', key, b'VALUES',
                           str(len(vector)).encode('ascii')]
                command.extend(_binary(value, 'vector component')
                               for value in vector)
                command.append(_binary(item.get('element'), 'vector element'))
                quantization = str(item.get('quantization', '')).upper()
                if quantization:
                    if quantization not in {'NOQUANT', 'BIN', 'Q8'}:
                        raise RedisClientError(
                            'vector quantization is invalid'
                        )
                    command.append(quantization.encode('ascii'))
                if item.get('attributes') is not None:
                    command.extend((b'SETATTR', _binary(json.dumps(
                        item['attributes'], separators=(',', ':')
                    ), 'vector attributes')))
                commands.append(tuple(command))
            return {'commands': commands}
        raise RedisClientError('Redis data operation is unavailable')

    @staticmethod
    def _append_set_options(command, options):
        options = _mapping(options, 'SET options')
        allowed = {'condition', 'ttl_ms', 'keep_ttl', 'return_old'}
        unknown = sorted(set(options).difference(allowed))
        if unknown:
            raise RedisClientError(
                'unknown SET options: ' + ', '.join(unknown)
            )
        condition = str(options.get('condition', '')).upper()
        if condition:
            if condition not in {'NX', 'XX'}:
                raise RedisClientError('SET condition must be NX or XX')
            command.append(condition.encode('ascii'))
        if options.get('ttl_ms') is not None:
            ttl = _bounded_int(options['ttl_ms'], None, 1, 2 ** 63 - 1,
                               'SET ttl_ms')
            command.extend((b'PX', str(ttl).encode('ascii')))
        if options.get('keep_ttl'):
            command.append(b'KEEPTTL')
        if options.get('return_old'):
            command.append(b'GET')

    @classmethod
    def _selector_values(cls, value, field):
        if isinstance(value, Mapping):
            value = value.get(field)
        if not isinstance(value, list) or not value:
            raise RedisClientError(f'{field} must be a non-empty array')
        return value

    @classmethod
    def _compile_consumer_group(cls, operation, draft, native):
        stream = native.get('stream', draft.get('stream'))
        group = native.get('group', draft.get('name'))
        stream = _binary(stream, 'stream key')
        group = _binary(group, 'consumer group')
        if operation == 'create':
            command = [b'XGROUP', b'CREATE', stream, group,
                       _binary(draft.get('start_id', '$'), 'start ID')]
            if draft.get('make_stream'):
                command.append(b'MKSTREAM')
            entries = draft.get('entries_read')
            if entries is not None:
                value = _bounded_int(entries, None, 0, 2 ** 63 - 1,
                                     'entries read')
                command.extend((b'ENTRIESREAD', str(value).encode('ascii')))
            return {'commands': [tuple(command)]}
        if operation == 'alter':
            command = [b'XGROUP', b'SETID', stream, group,
                       _binary(draft.get('start_id', '$'), 'start ID')]
            entries = draft.get('entries_read')
            if entries is not None:
                value = _bounded_int(entries, None, 0, 2 ** 63 - 1,
                                     'entries read')
                command.extend((b'ENTRIESREAD', str(value).encode('ascii')))
            return {'commands': [tuple(command)]}
        if operation == 'drop':
            return {'commands': [(b'XGROUP', b'DESTROY', stream, group)]}
        raise RedisClientError('consumer-group operation is unavailable')

    @classmethod
    def _compile_acl(cls, operation, draft, native, preview):
        name = native.get('username', draft.get('name'))
        username = _binary(name, 'ACL username')
        if operation == 'drop':
            return {'commands': [(b'ACL', b'DELUSER', username)]}
        rules = draft.get('rules', [])
        if not isinstance(rules, list) or any(
            not isinstance(item, str) or not item or '\x00' in item
            for item in rules
        ):
            raise RedisClientError('ACL rules must be an array of tokens')
        forbidden = [
            item for item in rules if item.startswith(('>', '<', '#'))
        ]
        if forbidden:
            raise RedisClientError(
                'ACL password material must use a credential reference'
            )
        command = [b'ACL', b'SETUSER', username]
        command.extend(item.encode('utf-8') for item in rules)
        reference = draft.get('password_credential_reference')
        if reference is not None:
            reference = _text(reference, 'ACL password credential reference')
            command.append(b'[credential-reference]' if preview else
                           b'__CDEADMIN_ACL_PASSWORD__')
        return {
            'commands': [tuple(command)],
            'password_reference': reference,
        }

    @classmethod
    def _compile_action(cls, kind, draft, native):
        action = _text(draft.get('action'), 'Redis action').lower()
        arguments = draft.get('arguments', {})
        if arguments is None:
            arguments = {}
        arguments = _mapping(arguments, 'Redis action arguments')
        allowed = {
            'deployment': {
                'save': (b'SAVE',), 'background-save': (b'BGSAVE',),
                'rewrite-aof': (b'BGREWRITEAOF',),
            },
            'node': {
                'ping': (b'PING',), 'reset-statistics': (b'CONFIG',
                                                         b'RESETSTAT'),
            },
            'replica': {
                'promote': (b'REPLICAOF', b'NO', b'ONE'),
                'readonly': (b'READONLY',), 'readwrite': (b'READWRITE',),
            },
            'cluster-slot': {
                'add': (b'CLUSTER', b'ADDSLOTS'),
                'delete': (b'CLUSTER', b'DELSLOTS'),
                'failover': (b'CLUSTER', b'FAILOVER'),
            },
            'pubsub-channel': {'publish': (b'PUBLISH',)},
            'script': {
                'flush': (b'SCRIPT', b'FLUSH', b'SYNC'),
                'kill': (b'SCRIPT', b'KILL'),
            },
            'persistence': {
                'save': (b'SAVE',), 'background-save': (b'BGSAVE',),
                'rewrite-aof': (b'BGREWRITEAOF',),
            },
            'configuration': {
                'rewrite': (b'CONFIG', b'REWRITE'),
                'reset-statistics': (b'CONFIG', b'RESETSTAT'),
            },
            'client': {
                'kill': (b'CLIENT', b'KILL'),
                'pause': (b'CLIENT', b'PAUSE'),
                'unpause': (b'CLIENT', b'UNPAUSE'),
            },
            'slow-log': {
                'reset': (b'SLOWLOG', b'RESET'),
            },
            'latency': {
                'reset': (b'LATENCY', b'RESET'),
            },
        }
        prefix = allowed.get(kind, {}).get(action)
        if prefix is None:
            raise RedisClientError(f'Redis {kind} action is not admitted')
        command = list(prefix)
        if kind == 'pubsub-channel':
            command.extend((
                cls._native_bytes(native, 'channel'),
                _binary(arguments.get('message'), 'message'),
            ))
        elif kind == 'cluster-slot' and action in {'add', 'delete'}:
            slots = cls._selector_values(arguments, 'slots')
            command.extend(_binary(value, 'cluster slot') for value in slots)
        elif kind == 'client' and action == 'kill':
            client_id = arguments.get('id', native.get('id'))
            command.extend((b'ID', _binary(client_id, 'client ID')))
        elif kind == 'client' and action == 'pause':
            timeout = _bounded_int(arguments.get('milliseconds'), None, 1,
                                   2 ** 63 - 1, 'pause milliseconds')
            command.append(str(timeout).encode('ascii'))
        elif kind in {'latency'} and arguments.get('event') is not None:
            command.append(_binary(arguments['event'], 'latency event'))
        return {'commands': [tuple(command)]}

    @staticmethod
    def _apply_sentinel(topology, payload, route):
        if topology is None:
            raise RedisClientError('Sentinel operation requires Sentinel mode')
        operation = payload['operation_id']
        draft = payload.get('draft', {})
        native = payload.get('native', {})
        service = native.get('service', route.get('sentinel_service'))
        if operation == 'inspect':
            value = topology.sentinel_master(service)
        elif operation == 'alter':
            changes = _mapping(draft.get('changes'), 'Sentinel changes')
            if len(changes) != 1:
                raise RedisClientError(
                    'Sentinel alteration accepts exactly one setting'
                )
            key, value = next(iter(changes.items()))
            value = topology.sentinel_set(service, key, value)
        elif operation == 'execute':
            action = _text(draft.get('action'), 'Sentinel action').lower()
            if action == 'failover':
                value = topology.sentinel_failover(service)
            elif action == 'reset':
                value = topology.sentinel_reset(service)
            else:
                raise RedisClientError('Sentinel action is not admitted')
        else:
            raise RedisClientError('Sentinel operation is unavailable')
        return {
            'provider_owned': True,
            'native_outcome': 'observed',
            'observations': RedisClient._json_value(value),
            'automatic_retry_performed': False,
            'common_finality_inference': False,
        }

    @staticmethod
    def _route_fingerprint(route):
        safe = {
            key: value for key, value in route.items()
            if key not in {'tool_workspace'}
        }
        return hashlib.sha256(json.dumps(
            safe, sort_keys=True, default=str
        ).encode('utf-8')).hexdigest()

    def _issue_identity(self, route, key, data_type, selector, value):
        if len(self._row_identities) >= MAX_RESULT_RECORDS:
            oldest = min(
                self._row_identities,
                key=lambda token: self._row_identities[token].issued_at,
            )
            self._row_identities.pop(oldest, None)
        token = str(uuid.uuid4())
        digest = self._generation(self._json_value(value))
        self._row_identities[token] = _RowIdentity(
            self._route_fingerprint(route), key, data_type,
            copy.deepcopy(selector), copy.deepcopy(value), digest,
            time.monotonic(),
        )
        return token

    def _resolve_row_identity(
        self, draft, native, data_type, route, consume=True
    ):
        selector = draft.get('selector')
        if not isinstance(selector, Mapping):
            return None
        token = selector.get('identity_token')
        if token is None:
            return None
        token = _text(token, 'Redis row identity token')
        identity = self._row_identities.get(token)
        if identity is None:
            raise RedisClientError('Redis row identity token is stale')
        if time.monotonic() - identity.issued_at > IDENTITY_TTL_SECONDS:
            self._row_identities.pop(token, None)
            raise RedisClientError('Redis row identity token has expired')
        if route is None or identity.route_fingerprint != (
            self._route_fingerprint(self._route({'route': route}))
        ):
            raise RedisClientError(
                'Redis row identity belongs to another endpoint route'
            )
        key = self._target_key(native, draft)
        if identity.key != key or identity.data_type != data_type:
            raise RedisClientError(
                'Redis row identity belongs to another key or data type'
            )
        if consume:
            self._row_identities.pop(token, None)
        return identity

    def _validate_row_identity(self, client, compiled):
        identity = compiled.get('row_identity')
        if identity is None:
            return
        key = identity.key
        selector = identity.selector
        kind = identity.data_type
        if kind in {'key', 'string', 'bitmap', 'hyperloglog'}:
            value = client.get(key)
        elif kind == 'hash':
            value = client.hget(key, selector)
        elif kind == 'list':
            value = client.lindex(key, selector)
        elif kind == 'set':
            value = selector if client.sismember(key, selector) else None
        elif kind == 'sorted-set':
            value = client.zscore(key, selector)
        elif kind == 'geospatial':
            positions = client.geopos(key, selector)
            position = positions[0] if positions else None
            if position is None:
                value = None
            else:
                value = {
                    'longitude': position[0], 'latitude': position[1],
                }
        elif kind == 'stream':
            rows = client.xrange(key, min=selector, max=selector, count=1)
            value = rows[0][1] if rows and rows[0][0] == selector else None
        elif kind == 'vector-set':
            value = client.execute_command('VEMB', key, selector)
        else:
            raise RedisClientError(
                'Redis row identity data type cannot be validated'
            )
        if self._generation(self._json_value(value)) != (
            identity.original_digest
        ):
            raise RedisClientError(
                'Redis value changed after it was read; reload before editing'
            )

    def read_admin_rows(self, request):
        route = self._route({'route': request.get('_provider_route')})
        target = self._native_target(request.get('target_resource'))
        kind = request['target_resource'].get(
            'resource_kind', target.get('data_type', 'key')
        )
        if kind not in self.DATA_KINDS:
            raise RedisClientError('Redis target is not an editable data type')
        key = self._target_key(target)
        limit = _bounded_int(
            request.get('limit'), 200, 1, MAX_PAGE_SIZE, 'row limit'
        )
        continuation = request.get('continuation')
        state = None
        if continuation is not None:
            state = self._admin_cursors.pop(str(continuation), None)
            if (
                state is None or
                state.route_fingerprint != self._route_fingerprint(route) or
                state.key != key or state.data_type != kind
            ):
                raise RedisClientError('Redis row continuation is unavailable')
        client = topology = None
        try:
            client, topology, _actual_route = self._connect({'route': route})
            rows, next_cursor = self._read_data_page(
                client, key, kind, limit,
                state.native_cursor if state else 0,
            )
            normalized = []
            for selector, value in rows:
                normalized.append({
                    'identity_token': self._issue_identity(
                        route, key, kind, selector, value
                    ),
                    'values': {
                        'selector': self._json_value(selector),
                        'value': self._json_value(value),
                    },
                })
            next_token = None
            if next_cursor is not None:
                if len(self._admin_cursors) >= MAX_ACTIVE_CURSORS:
                    raise RedisClientError('Redis row cursor limit reached')
                next_token = str(uuid.uuid4())
                self._admin_cursors[next_token] = _AdminCursor(
                    self._route_fingerprint(route), key, kind,
                    next_cursor, limit,
                )
            return {
                'schema': 'cdeadmin.visual-admin.key-value-page.v1',
                'resource_kind': kind,
                'columns': [
                    {'name': 'selector', 'type': 'redis-identity'},
                    {'name': 'value', 'type': 'redis-value'},
                ],
                'rows': normalized,
                'continuation': next_token,
                'complete': next_token is None,
                'editable': True,
                'identity_policy': (
                    'provider-route-key-type-selector-and-original-value'
                ),
            }
        finally:
            self._close_native(client, topology)

    @classmethod
    def _read_data_page(cls, client, key, kind, limit, cursor):
        if kind in {'key', 'string', 'bitmap', 'hyperloglog'}:
            value = client.get(key)
            return ([({'key': cls._json_value(key)}, value)]
                    if value is not None else []), None
        if kind == 'hash':
            next_cursor, values = client.hscan(key, cursor=cursor, count=limit)
            rows = list(values.items())
        elif kind == 'set':
            next_cursor, values = client.sscan(key, cursor=cursor, count=limit)
            rows = [(value, value) for value in values]
        elif kind == 'sorted-set':
            next_cursor, values = client.zscan(key, cursor=cursor, count=limit)
            rows = [(value, score) for value, score in values]
        elif kind == 'geospatial':
            next_cursor, values = client.zscan(key, cursor=cursor, count=limit)
            members = [value for value, _score in values]
            positions = client.geopos(key, *members) if members else []
            rows = []
            for member, position in zip(members, positions):
                longitude, latitude = position if position else (None, None)
                rows.append((member, {
                    'longitude': longitude, 'latitude': latitude,
                }))
        elif kind == 'list':
            values = client.lrange(key, cursor, cursor + limit - 1)
            rows = [(cursor + index, value)
                    for index, value in enumerate(values)]
            next_cursor = cursor + len(values) if len(values) == limit else 0
        elif kind == 'stream':
            start = '-' if cursor == 0 else b'(' + _binary(
                cursor, 'stream continuation'
            )
            values = client.xrange(key, min=start, max='+', count=limit)
            rows = list(values)
            next_cursor = (
                values[-1][0]
                if len(values) == limit else 0
            )
        elif kind == 'vector-set':
            start = '-' if cursor == 0 else b'(' + _binary(
                cursor, 'vector continuation'
            )
            values = client.execute_command(
                'VRANGE', key, start, '+', limit
            )
            rows = [(
                value, client.execute_command('VEMB', key, value)
            ) for value in values]
            next_cursor = values[-1] if len(values) == limit else 0
        else:
            raise RedisClientError('Redis data type is not editable')
        return rows, next_cursor or None

    def cancel_admin_cursor(self, request):
        token = str(request.get('continuation', ''))
        state = self._admin_cursors.get(token)
        route = self._route({'route': request.get('_provider_route')})
        if state is None or state.route_fingerprint != self._route_fingerprint(
            route
        ):
            raise RedisClientError('Redis row continuation is unavailable')
        self._admin_cursors.pop(token, None)
        return {'cancelled': True, 'continuation': token}

    @staticmethod
    def _workspace_path(workspace, value, label):
        root = Path(_text(workspace, 'Redis tool workspace', 4096)).resolve(
            strict=False
        )
        path = Path(_text(value, label, 4096))
        path = path if path.is_absolute() else root / path
        path = path.resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise RedisClientError(
                f'{label} escapes the tool workspace'
            ) from exc
        return path

    def _tool_grant(self, executable, route):
        workspace = route.get('tool_workspace')
        if not workspace:
            raise RedisClientError('Redis tool workspace is required')
        return ProviderToolGrant(
            executable, workspace, route['host'], route['port'],
            secret_environment_names=('REDISCLI_AUTH',),
        )

    def _tool_arguments(self, route):
        arguments = ['-h', route['host'], '-p', str(route['port']),
                     '-n', str(route['database']), '--resp3']
        if route.get('username'):
            arguments.extend(('--user', route['username']))
        if route['tls_mode'] != 'disabled':
            arguments.append('--tls')
            if route.get('tls_ca_file'):
                arguments.extend(('--cacert', route['tls_ca_file']))
            if route.get('tls_certificate_file'):
                arguments.extend(('--cert', route['tls_certificate_file'],
                                  '--key', route['tls_key_file']))
            if route['tls_mode'] == 'self-signed':
                arguments.append('--insecure')
        return arguments

    def _apply_tool(self, payload, route):
        kind = payload['resource_kind']
        draft = payload.get('draft', {})
        action = _text(draft.get('action'), 'Redis tool action').lower()
        arguments = _mapping(draft.get('arguments', {}), 'tool arguments')
        if kind == 'shell':
            commands = self._command_arrays([
                arguments.get('command')
            ])
            try:
                command = [item.decode('utf-8') for item in commands[0]]
            except UnicodeDecodeError as exc:
                raise RedisClientError(
                    'redis-cli command tokens must be UTF-8 text'
                ) from exc
            return self._run_redis_cli(
                route, [*self._tool_arguments(route), *command]
            )
        if kind in {'backup', 'export'} and action == 'rdb':
            output = self._workspace_path(
                route['tool_workspace'], arguments.get('file'), 'RDB output'
            )
            return self._run_redis_cli(
                route, [*self._tool_arguments(route), '--rdb', str(output)]
            )
        if kind in {'restore', 'import'} and action == 'pipe':
            source = self._workspace_path(
                route['tool_workspace'], arguments.get('file'), 'pipe input'
            )
            if not source.is_file():
                raise RedisClientError('Redis pipe input does not exist')
            return self._run_redis_cli(
                route, [*self._tool_arguments(route), '--pipe'],
                source.read_bytes(),
            )
        if kind == 'backup' and action in {'check-rdb', 'check-aof'}:
            source = self._workspace_path(
                route['tool_workspace'], arguments.get('file'), 'check input'
            )
            executable = (
                'redis-check-rdb' if action == 'check-rdb'
                else 'redis-check-aof'
            )
            return self._tool_runner.run(
                self._tool_grant(executable, route), [str(source)]
            )
        raise RedisClientError(f'Redis {kind} tool action is not admitted')

    def _run_redis_cli(self, route, arguments, input_bytes=b''):
        reference = route.get('credential_reference_id')

        def run(password=None):
            environment = {'REDISCLI_AUTH': password} if password else None
            return self._tool_runner.run(
                self._tool_grant('redis-cli', route), arguments,
                input_bytes=input_bytes,
                secret_environment=environment,
                redact_values=(password,) if password else (),
            )

        if reference is None:
            return run()
        if not callable(self._secret_acquirer):
            raise RedisClientError('Redis credential binding is unavailable')
        lease = self._secret_acquirer(
            reference, route['principal_reference'], 'execute',
            'database_password',
        )
        with lease:
            return lease.use(
                lambda value: run(bytes(value).decode('utf-8'))
            )

    def close(self):
        for session in self._sessions:
            session.close()
        self._sessions.clear()
        self._results.clear()
        self._row_identities.clear()
        self._admin_cursors.clear()
