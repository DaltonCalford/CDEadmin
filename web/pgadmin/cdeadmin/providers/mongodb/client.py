##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""PyMongo boundary for the MongoDB 8.2.6 semantic provider.

The adapter speaks the public MongoDB Query API and wire protocol.  It does
not identify, branch on, or otherwise special-case the server implementation
behind that protocol.  Driver transaction values are observations only; the
common CDEadmin layer never infers transaction finality from them.
"""

from __future__ import annotations

import copy
import importlib
import json
import math
import hashlib
import uuid
from itertools import islice
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from pgadmin.cdeadmin.sdk import (
    PilotProviderError,
    ProviderToolError,
    ProviderToolGrant,
    ProviderToolRunner,
)


MAX_RESULT_DOCUMENTS = 1000
MAX_RESOURCE_DOCUMENTS = 100
MAX_DATABASES = 200
MAX_COLLECTIONS = 2000
MAX_INDEXES = 4000
MAX_QUERY_BYTES = 2 * 1024 * 1024
MAX_BATCH_DOCUMENTS = 500
MAX_STREAM_DOCUMENTS = 100000
MAX_ACTIVE_CURSORS = 64


class MongoDBClientError(PilotProviderError):
    """A MongoDB dependency or native operation failed safely."""


class MongoDBDependencyError(MongoDBClientError):
    """The selected PyMongo dependency is unavailable."""


@dataclass
class _MongoSession:
    client: object
    driver_session: object
    default_database: str
    closed: bool = False

    def close(self):
        if self.closed:
            return
        try:
            self.driver_session.end_session()
        finally:
            self.client.close()
            self.closed = True


@dataclass
class _MongoResult:
    documents: list[object]
    operation: str
    database: str
    collection: str | None
    acknowledged: bool = True
    cancelled: bool = False
    cursor: object | None = None
    batch_size: int = 200
    max_documents: int = MAX_STREAM_DOCUMENTS
    emitted: int = 0
    complete: bool = False
    live: bool = False
    resume_token: object | None = None
    pending: list[object] = field(default_factory=list)

    def close_cursor(self):
        close = getattr(self.cursor, 'close', None)
        if callable(close):
            close()
        self.cursor = None


@dataclass
class _MongoAdminCursor:
    cursor_id: str
    cursor: object
    database: str
    collection: str
    batch_size: int
    client: object

    def close(self):
        close = getattr(self.cursor, 'close', None)
        if callable(close):
            close()
        close_client = getattr(self.client, 'close', None)
        if callable(close_client):
            close_client()


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MongoDBClientError(f'{label} must not be empty')
    value = value.strip()
    if '\x00' in value:
        raise MongoDBClientError(f'{label} contains a forbidden character')
    return value


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MongoDBClientError(f'{label} must be an object')
    return copy.deepcopy(dict(value))


def _bounded_int(value: object, default: int, minimum: int,
                 maximum: int, label: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise MongoDBClientError(f'{label} must be an integer')
    if not minimum <= value <= maximum:
        raise MongoDBClientError(
            f'{label} must be between {minimum} and {maximum}'
        )
    return value


class MongoDBClient:
    """Synchronous, bounded PyMongo port for the actual-engine SDK."""

    transaction_actions = ('begin', 'commit', 'rollback')

    READ_COMMANDS = frozenset({
        'buildInfo', 'collStats', 'connectionStatus', 'count', 'dbStats',
        'distinct', 'explain', 'hello', 'listCollections', 'listDatabases',
        'listIndexes', 'ping', 'replSetGetStatus', 'serverStatus',
    })
    ROUTE_KEYS = frozenset({
        'appname', 'auth_mechanism', 'auth_source', 'compressors', 'dns_srv',
        'contact_points', 'connect_timeout_ms', 'credential_kind',
        'credential_reference_id', 'database', 'direct_connection', 'host',
        'connection_timeout', 'credential_kinds', 'credential_references',
        'gssapi_canonicalize_host_name', 'gssapi_service_name',
        'heartbeat_frequency_ms', 'load_balanced', 'max_connecting',
        'max_adaptive_retries', 'enable_overload_retargeting',
        'max_idle_time_ms', 'max_pool_size', 'min_pool_size', 'port',
        'principal_reference', 'read_concern_level', 'read_preference',
        'read_preference_tags', 'max_staleness_seconds',
        'local_threshold_ms', 'write_concern_timeout_ms', 'journal',
        'session_causal_consistency', 'session_snapshot',
        'transaction_read_concern', 'transaction_write_concern',
        'transaction_max_commit_time_ms', 'operation_timeout_ms',
        'oidc_environment', 'oidc_token_resource', 'replica_set',
        'retry_reads', 'retry_writes', 'route_id', 'srv_max_hosts',
        'srv_service_name',
        'server_api_version', 'server_api_strict',
        'server_api_deprecation_errors', 'server_monitoring_mode',
        'server_selection_timeout_ms', 'socket_timeout_ms', 'tls',
        'tls_allow_invalid_certificates', 'tls_allow_invalid_hostnames',
        'tls_ca_file', 'tls_certificate_key_file', 'tls_crl_file',
        'tls_disable_ocsp_endpoint_check',
        'tool_workspace', 'user', 'username', 'wait_queue_timeout_ms',
        'wait_queue_multiple', 'write_concern', 'fsync',
        'auth_oidc_allowed_hosts', 'tz_aware', 'uuid_representation',
        'unicode_decode_error_handler', 'zlib_compression_level',
    })
    AUTH_MECHANISMS = frozenset({
        'NONE', 'DEFAULT', 'SCRAM-SHA-1', 'SCRAM-SHA-256',
        'MONGODB-X509', 'GSSAPI', 'PLAIN', 'MONGODB-AWS',
        'MONGODB-OIDC',
    })

    def __init__(self, secret_acquirer=None, module=None):
        try:
            self.module = module or importlib.import_module('pymongo')
            bson_module = importlib.import_module('bson.json_util')
        except (ImportError, ModuleNotFoundError) as exc:
            raise MongoDBDependencyError(
                'MongoDB client dependency pymongo is unavailable'
            ) from exc
        connector = getattr(self.module, 'MongoClient', None)
        if not callable(connector):
            raise MongoDBDependencyError(
                'PyMongo has no approved MongoClient connector'
            )
        self._connector = connector
        self._json_util = bson_module
        self._secret_acquirer = secret_acquirer
        self._clients: list[object] = []
        self._sessions: list[_MongoSession] = []
        self._results: list[_MongoResult] = []
        self._admin_cursors: dict[str, _MongoAdminCursor] = {}
        self._tool_runner = ProviderToolRunner({
            'mongodump': 'mongodump',
            'mongorestore': 'mongorestore',
            'mongoexport': 'mongoexport',
            'mongoimport': 'mongoimport',
            'mongosh': 'mongosh',
        })

    @staticmethod
    def _route(request: Mapping[str, Any]) -> dict[str, Any]:
        route = request.get('route', request.get('_provider_route'))
        route = _mapping(route, 'MongoDB route')
        unknown = sorted(set(route).difference(MongoDBClient.ROUTE_KEYS))
        if unknown:
            raise MongoDBClientError(
                'MongoDB route contains unknown fields: ' +
                ', '.join(unknown)
            )
        if route.get('user') is not None:
            if route.get('username') not in {None, route['user']}:
                raise MongoDBClientError(
                    'MongoDB route user aliases disagree'
                )
            route['username'] = route.pop('user')
        timeout = route.pop('connection_timeout', None)
        if timeout is not None:
            seconds = _bounded_int(
                timeout, 10, 1, 120, 'connection timeout'
            )
            route.setdefault('connect_timeout_ms', seconds * 1000)
            route.setdefault('server_selection_timeout_ms', seconds * 1000)
        # URI routes can conceal credentials and ambiguous driver options.
        # The provider deliberately accepts only an explicit structured route.
        _identifier(route.get('host'), 'MongoDB route host')
        _bounded_int(route.get('port'), 27017, 1, 65535, 'MongoDB port')
        mechanism = route.get('auth_mechanism', 'DEFAULT')
        if mechanism not in MongoDBClient.AUTH_MECHANISMS:
            raise MongoDBClientError(
                'MongoDB authentication mechanism is invalid'
            )
        route['auth_mechanism'] = mechanism
        if route.get('load_balanced') and any((
            route.get('replica_set'), route.get('direct_connection'),
        )):
            raise MongoDBClientError(
                'MongoDB load-balanced mode conflicts with direct or '
                'replica-set routing'
            )
        if route.get('dns_srv') and any((
            route.get('contact_points'), route.get('direct_connection'),
        )):
            raise MongoDBClientError(
                'MongoDB DNS SRV conflicts with explicit seeds or direct '
                'routing'
            )
        compressors = route.get('compressors')
        if compressors:
            values = [
                item.strip() for item in compressors.split(',')
                if item.strip()
            ]
            if not values or set(values) - {'snappy', 'zlib', 'zstd'}:
                raise MongoDBClientError(
                    'MongoDB compressor selection is invalid'
                )
            route['compressors'] = ','.join(dict.fromkeys(values))
        self_monitoring = route.get('server_monitoring_mode', 'auto')
        if self_monitoring not in {'auto', 'stream', 'poll'}:
            raise MongoDBClientError(
                'MongoDB server monitoring mode is invalid'
            )
        route['server_monitoring_mode'] = self_monitoring
        api_version = route.get('server_api_version')
        if api_version not in {None, '1'}:
            raise MongoDBClientError('MongoDB server API version is invalid')
        uuid_representation = route.get('uuid_representation', 'standard')
        if uuid_representation not in {
            'unspecified', 'standard', 'pythonLegacy', 'javaLegacy',
            'csharpLegacy',
        }:
            raise MongoDBClientError(
                'MongoDB UUID representation is invalid'
            )
        route['uuid_representation'] = uuid_representation
        decode_handler = route.get('unicode_decode_error_handler', 'strict')
        if decode_handler not in {'strict', 'replace', 'ignore'}:
            raise MongoDBClientError(
                'MongoDB Unicode error handler is invalid'
            )
        route['unicode_decode_error_handler'] = decode_handler
        for name in (
            'direct_connection', 'load_balanced', 'tls',
            'tls_allow_invalid_certificates',
            'tls_allow_invalid_hostnames',
            'tls_disable_ocsp_endpoint_check', 'retry_reads',
            'retry_writes', 'journal', 'fsync', 'tz_aware',
            'server_api_strict', 'server_api_deprecation_errors',
            'enable_overload_retargeting',
        ):
            if name in route and not isinstance(route[name], bool):
                raise MongoDBClientError(
                    f'MongoDB {name} must be true or false'
                )
        bounds = {
            'connect_timeout_ms': (100, 120000),
            'server_selection_timeout_ms': (100, 120000),
            'socket_timeout_ms': (100, 600000),
            'operation_timeout_ms': (0, 86400000),
            'wait_queue_timeout_ms': (0, 3600000),
            'heartbeat_frequency_ms': (500, 120000),
            'local_threshold_ms': (0, 600000),
            'write_concern_timeout_ms': (0, 86400000),
            'min_pool_size': (0, 10000),
            'max_pool_size': (1, 10000),
            'max_connecting': (1, 1000),
            'max_idle_time_ms': (0, 86400000),
            'srv_max_hosts': (0, 10000),
            'wait_queue_multiple': (0, 10000),
            'max_adaptive_retries': (0, 1000),
        }
        for name, (minimum, maximum) in bounds.items():
            if name in route:
                _bounded_int(route[name], 0, minimum, maximum, name)
        if (
            route.get('min_pool_size') is not None and
            route.get('max_pool_size') is not None and
            route['min_pool_size'] > route['max_pool_size']
        ):
            raise MongoDBClientError(
                'MongoDB minimum pool size exceeds maximum pool size'
            )
        allowed_hosts = route.get('auth_oidc_allowed_hosts')
        if allowed_hosts is not None and (
            not isinstance(allowed_hosts, list) or not allowed_hosts or
            not all(isinstance(item, str) and item.strip()
                    for item in allowed_hosts)
        ):
            raise MongoDBClientError(
                'MongoDB OIDC allowed hosts must be a non-empty text array'
            )
        return route

    def _connector_arguments(self, route):
        port = _bounded_int(
            route.get('port'), 27017, 1, 65535, 'MongoDB port'
        )
        hosts = [route['host']]
        for value in str(route.get('contact_points') or '').split(','):
            value = value.strip()
            if value and value not in hosts:
                hosts.append(value)
        dns_srv = route.get('dns_srv') is True
        arguments = {
            'host': (
                f'mongodb+srv://{route["host"]}' if dns_srv else
                hosts if len(hosts) > 1 else hosts[0]
            ),
            'connect': True,
            'tz_aware': True,
            'uuidRepresentation': route.get(
                'uuid_representation', 'standard'
            ),
            'unicode_decode_error_handler': route.get(
                'unicode_decode_error_handler', 'strict'
            ),
            'serverSelectionTimeoutMS': _bounded_int(
                route.get('server_selection_timeout_ms'), 5000,
                100, 120000, 'server selection timeout'
            ),
            'connectTimeoutMS': _bounded_int(
                route.get('connect_timeout_ms'), 5000,
                100, 120000, 'connect timeout'
            ),
            'socketTimeoutMS': _bounded_int(
                route.get('socket_timeout_ms'), 30000,
                100, 600000, 'socket timeout'
            ),
            'appname': route.get('appname', 'CDEadmin'),
        }
        if 'tz_aware' in route:
            arguments['tz_aware'] = route['tz_aware']
        if len(hosts) == 1 and not dns_srv:
            arguments['port'] = port
        mechanism = route.get('auth_mechanism', 'DEFAULT')
        properties = self._auth_mechanism_properties(route)
        optional = {
            'username': (
                None if mechanism == 'NONE' else route.get('username')
            ),
            'authSource': (
                route.get('auth_source') or route.get('database')
            ),
            'authMechanism': (
                None if mechanism in {'NONE', 'DEFAULT'} else mechanism
            ),
            'authMechanismProperties': properties or None,
            'replicaSet': route.get('replica_set'),
            'directConnection': route.get('direct_connection'),
            'loadBalanced': route.get('load_balanced'),
            'tls': route.get('tls'),
            'tlsCAFile': route.get('tls_ca_file'),
            'tlsCertificateKeyFile': route.get(
                'tls_certificate_key_file'
            ),
            'tlsCRLFile': route.get('tls_crl_file'),
            'tlsAllowInvalidCertificates': route.get(
                'tls_allow_invalid_certificates'
            ),
            'tlsAllowInvalidHostnames': route.get(
                'tls_allow_invalid_hostnames'
            ),
            'tlsDisableOCSPEndpointCheck': route.get(
                'tls_disable_ocsp_endpoint_check'
            ),
            'compressors': route.get('compressors'),
            'zlibCompressionLevel': route.get('zlib_compression_level'),
            'readPreference': route.get('read_preference'),
            'readPreferenceTags': route.get('read_preference_tags'),
            'maxStalenessSeconds': route.get('max_staleness_seconds'),
            'localThresholdMS': route.get('local_threshold_ms'),
            'readConcernLevel': route.get('read_concern_level'),
            'w': self._write_concern(route.get('write_concern')),
            'wTimeoutMS': route.get('write_concern_timeout_ms'),
            'journal': route.get('journal'),
            'retryReads': route.get('retry_reads'),
            'retryWrites': route.get('retry_writes'),
            'minPoolSize': route.get('min_pool_size'),
            'maxPoolSize': route.get('max_pool_size'),
            'maxConnecting': route.get('max_connecting'),
            'maxIdleTimeMS': route.get('max_idle_time_ms'),
            'waitQueueTimeoutMS': route.get('wait_queue_timeout_ms'),
            'waitQueueMultiple': route.get('wait_queue_multiple'),
            'heartbeatFrequencyMS': route.get('heartbeat_frequency_ms'),
            'serverMonitoringMode': route.get('server_monitoring_mode'),
            'maxAdaptiveRetries': route.get('max_adaptive_retries'),
            'enableOverloadRetargeting': route.get(
                'enable_overload_retargeting'
            ),
            'srvServiceName': route.get('srv_service_name'),
            'srvMaxHosts': route.get('srv_max_hosts'),
            'authOIDCAllowedHosts': route.get('auth_oidc_allowed_hosts'),
            'fsync': route.get('fsync'),
            'timeoutMS': route.get('operation_timeout_ms'),
        }
        arguments.update({
            key: value for key, value in optional.items()
            if value is not None
        })
        api_version = route.get('server_api_version')
        if api_version is not None:
            try:
                server_api_module = importlib.import_module(
                    'pymongo.server_api'
                )
                arguments['server_api'] = server_api_module.ServerApi(
                    api_version,
                    strict=route.get('server_api_strict'),
                    deprecation_errors=route.get(
                        'server_api_deprecation_errors'
                    ),
                )
            except (AttributeError, ImportError, ModuleNotFoundError) as exc:
                raise MongoDBDependencyError(
                    'PyMongo Stable API support is unavailable'
                ) from exc
        return arguments

    @staticmethod
    def _auth_mechanism_properties(route):
        mechanism = route.get('auth_mechanism', 'DEFAULT')
        if mechanism == 'GSSAPI':
            return {
                'SERVICE_NAME': route.get('gssapi_service_name', 'mongodb'),
                'CANONICALIZE_HOST_NAME': (
                    'true' if route.get('gssapi_canonicalize_host_name')
                    else 'false'
                ),
            }
        if mechanism == 'MONGODB-OIDC' and route.get(
            'oidc_environment'
        ) not in {None, 'callback'}:
            result = {'ENVIRONMENT': route['oidc_environment']}
            if route.get('oidc_token_resource'):
                result['TOKEN_RESOURCE'] = route['oidc_token_resource']
            return result
        return {}

    @staticmethod
    def _write_concern(value):
        if value is None:
            return None
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return value

    def _connect(self, request):
        route = self._route(request)
        reference_id = route.get('credential_reference_id')
        principal = route.get('principal_reference')
        arguments = self._connector_arguments(route)
        try:
            references = dict(route.get('credential_references') or {})
            if reference_id is not None:
                references.setdefault(
                    route.get('credential_kind', 'database_password'),
                    reference_id,
                )
            if references:
                for value in references.values():
                    _identifier(value, 'credential reference')
                _identifier(principal, 'principal reference')
                if not callable(self._secret_acquirer):
                    raise MongoDBClientError(
                        'MongoDB credential binding is unavailable'
                    )
                client = self._connect_with_credentials(
                    arguments, references, principal,
                    route['auth_mechanism'],
                )
            else:
                client = self._connector(**arguments)
            client.admin.command('ping')
        except MongoDBClientError:
            raise
        except Exception as exc:
            raise MongoDBClientError(
                f'MongoDB connection failed ({type(exc).__name__})'
            ) from None
        self._clients.append(client)
        return client, route

    def _connect_with_credentials(
        self, arguments, references, principal, mechanism
    ):
        bindings = []
        if mechanism in {
            'DEFAULT', 'SCRAM-SHA-1', 'SCRAM-SHA-256', 'PLAIN', 'GSSAPI'
        } and 'database_password' in references:
            bindings.append(('database_password', 'password'))
        if mechanism == 'MONGODB-AWS':
            bindings.append(('cloud_secret_access_key', 'password'))
        if 'tls_private_key_password' in references:
            bindings.append((
                'tls_private_key_password',
                'tlsCertificateKeyFilePassword',
            ))

        def connect(index, options):
            if index == len(bindings):
                if 'cloud_session_token' in references:
                    kind = 'cloud_session_token'
                    lease = self._secret_acquirer(
                        references[kind], principal, 'connect', kind
                    )
                    with lease:
                        return lease.use(lambda view: self._connector(**{
                            **options,
                            'authMechanismProperties': {
                                **dict(options.get(
                                    'authMechanismProperties'
                                ) or {}),
                                'AWS_SESSION_TOKEN': bytes(view).decode(
                                    'utf-8'
                                ),
                            },
                        }))
                return self._connector(**options)
            kind, argument = bindings[index]
            reference = references.get(kind)
            if reference is None:
                raise MongoDBClientError(
                    f'MongoDB credential {kind} is unavailable'
                )
            lease = self._secret_acquirer(
                reference, principal, 'connect', kind
            )
            with lease:
                return lease.use(lambda view: connect(index + 1, {
                    **options,
                    argument: bytes(view).decode('utf-8'),
                }))

        if mechanism == 'MONGODB-OIDC' and 'oidc_access_token' in references:
            arguments = dict(arguments)
            arguments['authMechanismProperties'] = {
                **dict(arguments.get('authMechanismProperties') or {}),
                'OIDC_MACHINE_CALLBACK': self._oidc_callback(
                    references['oidc_access_token'], principal
                ),
            }
        return connect(0, arguments)

    def _oidc_callback(self, reference, principal):
        try:
            oidc = importlib.import_module('pymongo.auth_oidc')
            base = oidc.OIDCCallback
            result_type = oidc.OIDCCallbackResult
        except (AttributeError, ImportError, ModuleNotFoundError):
            raise MongoDBDependencyError(
                'PyMongo OIDC callback support is unavailable'
            ) from None
        acquire = self._secret_acquirer

        class LeasedOIDCCallback(base):
            def fetch(self, _context):
                lease = acquire(
                    reference, principal, 'connect', 'oidc_access_token'
                )
                with lease:
                    return lease.use(lambda view: result_type(
                        access_token=bytes(view).decode('utf-8')
                    ))

        return LeasedOIDCCallback()

    def _forget_client(self, client):
        if client in self._clients:
            self._clients.remove(client)
        try:
            client.close()
        except Exception:
            pass

    def _extended_json(self, value):
        serialized = self._json_util.dumps(
            value, json_options=self._json_util.CANONICAL_JSON_OPTIONS,
            ensure_ascii=False,
        )
        return json.loads(serialized)

    def _from_extended_json(self, value):
        return self._json_util.loads(json.dumps(
            value, ensure_ascii=False, separators=(',', ':'),
        ))

    def runtime_identity(self, request, handle=None):
        temporary = handle is None
        if temporary:
            client, _route = self._connect(request)
        else:
            if not isinstance(handle, _MongoSession) or handle.closed:
                raise MongoDBClientError('MongoDB session is unavailable')
            client = handle.client
        try:
            build = client.admin.command('buildInfo')
            hello = client.admin.command('hello')
            version = _identifier(build.get('version'), 'MongoDB version')
            build_id = str(build.get('gitVersion') or version)
            return {
                'engine_id': 'mongodb',
                'version': version,
                'build_id': build_id,
                'protocol_id': 'mongodb_wire',
                'native': {
                    'max_wire_version': hello.get('maxWireVersion'),
                    'min_wire_version': hello.get('minWireVersion'),
                },
            }
        except MongoDBClientError:
            raise
        except Exception as exc:
            raise MongoDBClientError(
                'MongoDB profile verification failed '
                f'({type(exc).__name__})'
            ) from None
        finally:
            if temporary:
                self._forget_client(client)

    def open_session(self, request):
        client, route = self._connect(request)
        try:
            driver_session = client.start_session(
                **self._session_options(route)
            )
        except MongoDBClientError:
            self._forget_client(client)
            raise
        except Exception as exc:
            self._forget_client(client)
            raise MongoDBClientError(
                f'MongoDB session creation failed ({type(exc).__name__})'
            ) from None
        handle = _MongoSession(
            client, driver_session, str(route.get('database') or 'admin')
        )
        self._sessions.append(handle)
        return handle

    def _session_options(self, route):
        causal = route.get('session_causal_consistency', True) is not False
        snapshot = route.get('session_snapshot', False) is True
        if causal and snapshot:
            raise MongoDBClientError(
                'MongoDB snapshot and causally consistent session modes '
                'are mutually exclusive'
            )
        options = {
            'causal_consistency': causal,
            'snapshot': snapshot,
        }
        read_level = route.get('transaction_read_concern', 'server')
        write_value = route.get('transaction_write_concern')
        commit_time = route.get('transaction_max_commit_time_ms')
        if read_level != 'server' or write_value is not None or (
            commit_time is not None
        ):
            try:
                session_module = importlib.import_module(
                    'pymongo.client_session'
                )
                concern_module = importlib.import_module(
                    'pymongo.read_concern'
                )
                write_module = importlib.import_module(
                    'pymongo.write_concern'
                )
            except (ImportError, ModuleNotFoundError) as exc:
                raise MongoDBDependencyError(
                    'PyMongo transaction option support is unavailable'
                ) from exc
            arguments = {}
            if read_level != 'server':
                if read_level not in {'local', 'majority', 'snapshot'}:
                    raise MongoDBClientError(
                        'MongoDB transaction read concern is invalid'
                    )
                arguments['read_concern'] = concern_module.ReadConcern(
                    read_level
                )
            if write_value is not None:
                arguments['write_concern'] = write_module.WriteConcern(
                    w=self._write_concern(write_value),
                    wtimeout=route.get('write_concern_timeout_ms'),
                    j=route.get('journal'),
                )
            if commit_time is not None:
                arguments['max_commit_time_ms'] = _bounded_int(
                    commit_time, 0, 1, 86400000,
                    'transaction maximum commit time',
                )
            options['default_transaction_options'] = (
                session_module.TransactionOptions(**arguments)
            )
        return options

    def describe_transaction(self, handle):
        if not isinstance(handle, _MongoSession) or handle.closed:
            raise MongoDBClientError('MongoDB session is unavailable')
        session = handle.driver_session
        return {
            'driver_observation_only': True,
            'finality_interpreted_by_common_code': False,
            'in_transaction': bool(getattr(session, 'in_transaction', False)),
            'has_ended': bool(getattr(session, 'has_ended', False)),
            'session_id': self._extended_json(
                getattr(session, 'session_id', None)
            ),
        }

    def control_transaction(self, handle, action):
        """Use PyMongo's explicit session transaction API without retries."""
        if not isinstance(handle, _MongoSession) or handle.closed:
            raise MongoDBClientError('MongoDB session is unavailable')
        callbacks = {
            'begin': 'start_transaction',
            'commit': 'commit_transaction',
            'rollback': 'abort_transaction',
        }
        callback_name = callbacks.get(action)
        callback = getattr(handle.driver_session, callback_name, None)
        if not callable(callback):
            raise MongoDBClientError(
                'MongoDB transaction action is unavailable'
            )
        try:
            callback()
        except Exception as exc:
            raise MongoDBClientError(
                'MongoDB transaction action outcome is driver-owned '
                f'({type(exc).__name__})'
            ) from None

    def execute(self, handle, request):
        if not isinstance(handle, _MongoSession) or handle.closed:
            raise MongoDBClientError('MongoDB session is unavailable')
        source = request.get('source')
        if not isinstance(source, str) or not source.strip():
            raise MongoDBClientError('MongoDB Query API source is required')
        if len(source.encode('utf-8')) > MAX_QUERY_BYTES:
            raise MongoDBClientError('MongoDB Query API source is too large')
        if sum(result.cursor is not None for result in self._results) >= (
            MAX_ACTIVE_CURSORS
        ):
            raise MongoDBClientError(
                'MongoDB active query cursor limit is reached'
            )
        try:
            query = self._json_util.loads(source)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            position = getattr(exc, 'pos', 'unknown')
            raise MongoDBClientError(
                f'MongoDB Query API source is invalid JSON at {position}'
            ) from None
        query = _mapping(query, 'MongoDB Query API source')
        operation = _identifier(query.get('operation'), 'operation')
        database_name = _identifier(
            query.get('database', handle.default_database), 'database'
        )
        database = handle.client[database_name]
        collection_name = query.get('collection')
        documents = []
        cursor = None
        live = False
        complete = False
        batch_size = _bounded_int(
            query.get('batch_size'), 200, 1, MAX_BATCH_DOCUMENTS,
            'batch size'
        )
        max_documents = _bounded_int(
            query.get('max_documents'), MAX_RESULT_DOCUMENTS, 1,
            MAX_STREAM_DOCUMENTS, 'maximum documents'
        )
        try:
            if operation == 'find':
                collection_name = _identifier(
                    collection_name, 'collection'
                )
                limit = _bounded_int(
                    query.get('limit'), max_documents, 1,
                    MAX_STREAM_DOCUMENTS,
                    'find limit'
                )
                max_documents = min(max_documents, limit)
                cursor = database[collection_name].find(
                    _mapping(query.get('filter', {}), 'find filter'),
                    query.get('projection'), session=handle.driver_session,
                )
                sort = query.get('sort')
                if sort is not None:
                    if not isinstance(sort, list):
                        raise MongoDBClientError('find sort must be an array')
                    cursor = cursor.sort(sort)
                cursor = cursor.skip(_bounded_int(
                    query.get('skip'), 0, 0, 100000000, 'find skip'
                )).limit(max_documents)
                set_batch_size = getattr(cursor, 'batch_size', None)
                if callable(set_batch_size):
                    cursor = set_batch_size(batch_size)
            elif operation == 'aggregate':
                collection_name = _identifier(
                    collection_name, 'collection'
                )
                pipeline = query.get('pipeline')
                if not isinstance(pipeline, list):
                    raise MongoDBClientError(
                        'aggregate pipeline must be an array'
                    )
                cursor = database[collection_name].aggregate(
                    pipeline, session=handle.driver_session,
                    batchSize=batch_size,
                )
            elif operation == 'watch':
                collection_name = _identifier(
                    collection_name, 'collection'
                )
                pipeline = query.get('pipeline', [])
                if not isinstance(pipeline, list):
                    raise MongoDBClientError(
                        'change-stream pipeline must be an array'
                    )
                options = {
                    'pipeline': pipeline,
                    'session': handle.driver_session,
                    'batch_size': batch_size,
                    'max_await_time_ms': _bounded_int(
                        query.get('max_await_time_ms'), 1000, 50, 60000,
                        'change-stream maximum await time'
                    ),
                }
                for source, target in (
                    ('resume_after', 'resume_after'),
                    ('start_after', 'start_after'),
                    ('start_at_operation_time', 'start_at_operation_time'),
                    ('full_document', 'full_document'),
                    ('full_document_before_change',
                     'full_document_before_change'),
                    ('show_expanded_events', 'show_expanded_events'),
                ):
                    if query.get(source) is not None:
                        options[target] = query[source]
                if sum(
                    query.get(name) is not None for name in (
                        'resume_after', 'start_after',
                        'start_at_operation_time',
                    )
                ) > 1:
                    raise MongoDBClientError(
                        'change-stream resume boundaries are mutually '
                        'exclusive'
                    )
                cursor = database[collection_name].watch(**options)
                live = True
            elif operation == 'command':
                command = _mapping(query.get('command'), 'command')
                name = next(iter(command), None)
                if name not in self.READ_COMMANDS:
                    raise MongoDBClientError(
                        'command is not admitted by the read-only query port'
                    )
                documents = [database.command(
                    command, session=handle.driver_session
                )]
                complete = True
            else:
                raise MongoDBClientError(
                    'MongoDB Query API operation is unsupported'
                )
        except MongoDBClientError:
            raise
        except Exception as exc:
            raise MongoDBClientError(
                f'MongoDB query execution failed ({type(exc).__name__})'
            ) from None
        token = _MongoResult(
            [self._extended_json(item) for item in documents], operation,
            database_name, collection_name, cursor=cursor,
            batch_size=batch_size, max_documents=max_documents,
            emitted=len(documents), complete=complete, live=live,
        )
        self._results.append(token)
        return token

    def describe_result(self, token):
        if not isinstance(token, _MongoResult) or token not in self._results:
            raise MongoDBClientError('MongoDB result token is invalid')
        if token.cursor is not None and not token.cancelled:
            remaining = token.max_documents - token.emitted
            requested = min(token.batch_size, max(remaining, 0))
            batch = []
            try:
                if requested == 0:
                    token.complete = True
                elif token.live:
                    try_next = getattr(token.cursor, 'try_next', None)
                    if not callable(try_next):
                        raise MongoDBClientError(
                            'MongoDB change stream has no non-blocking reader'
                        )
                    for _offset in range(requested):
                        document = try_next()
                        if document is None:
                            break
                        batch.append(document)
                    token.resume_token = getattr(
                        token.cursor, 'resume_token', token.resume_token
                    )
                else:
                    batch = list(islice(token.cursor, requested))
                    if len(batch) < requested:
                        token.complete = True
                token.documents = [
                    self._extended_json(item) for item in batch
                ]
                token.emitted += len(batch)
                if token.emitted >= token.max_documents:
                    token.complete = True
                if token.complete:
                    token.close_cursor()
            except MongoDBClientError:
                token.close_cursor()
                raise
            except Exception as exc:
                token.close_cursor()
                raise MongoDBClientError(
                    'MongoDB cursor retrieval failed '
                    f'({type(exc).__name__})'
                ) from None
        return {
            'result_kind': 'document',
            'schema': {
                'encoding': 'mongodb-canonical-extended-json',
                'fields': [],
            },
            'payload': {
                'documents': copy.deepcopy(token.documents),
                'operation': token.operation,
                'database': token.database,
                'collection': token.collection,
                'acknowledged': token.acknowledged,
                'cancelled': token.cancelled,
                'batch_size': token.batch_size,
                'emitted': token.emitted,
                'live': token.live,
                'resume_token': (
                    self._extended_json(token.resume_token)
                    if token.resume_token is not None else None
                ),
            },
            'stream_reference': (
                {
                    'kind': 'mongodb-change-stream' if token.live
                    else 'mongodb-cursor',
                    'database': token.database,
                    'collection': token.collection,
                }
                if token.cursor is not None else None
            ),
            'complete': token.complete or token.cancelled,
        }

    def cancel(self, token):
        if not isinstance(token, _MongoResult) or token not in self._results:
            raise MongoDBClientError('MongoDB result token is invalid')
        if token.complete or token.cancelled:
            return False
        token.cancelled = True
        token.complete = True
        token.close_cursor()
        # Cancellation is a cursor observation only and is never interpreted
        # as transaction finality by the common layer.
        return True

    @staticmethod
    def _part(value):
        return quote(str(value), safe='')

    def _generation(self, build, hello, topology_catalog=None):
        topology = hello.get('topologyVersion', {})
        value = {
            'build': build.get('gitVersion') or build.get('version'),
            'set': hello.get('setName'),
            'primary': hello.get('primary'),
            'hosts': hello.get('hosts', []),
            'topology': self._extended_json(topology),
            'catalog': self._extended_json(topology_catalog or {}),
        }
        return hashlib.sha256(json.dumps(
            value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
        ).encode('utf-8')).hexdigest()

    def _resource(self, kind, path, name, generation, native=None,
                  virtual=False):
        path = [str(item) for item in path]
        native = self._extended_json(native or {})
        if kind in {'collection', 'view'} and 'options' in native:
            native['definition'] = copy.deepcopy(native['options'])
        elif kind == 'validator' and 'validator' in native:
            native['definition'] = copy.deepcopy(native['validator'])
        elif kind == 'index' and 'index' in native:
            native['definition'] = copy.deepcopy(native['index'])
            index = self._from_extended_json(native['index'])
            ttl = index.get('expireAfterSeconds')
            if isinstance(ttl, int) and not isinstance(ttl, bool):
                native['editor_values'] = {'ttl_seconds': int(ttl)}
        elif kind == 'document' and 'document' in native:
            native['data'] = copy.deepcopy(native['document'])
        elif kind == 'statistics' and 'stats' in native:
            native['statistics'] = copy.deepcopy(native['stats'])
        elif kind == 'profiling' and 'profile' in native:
            native['state'] = copy.deepcopy(native['profile'])
        elif kind == 'user' and 'roles' in native:
            native['security'] = {'roles': copy.deepcopy(native['roles'])}
        elif kind == 'role':
            native['security'] = {
                'inherited_roles': copy.deepcopy(native.get('roles', [])),
            }
            if 'privileges' in native:
                native['privileges'] = copy.deepcopy(native['privileges'])
        elif kind == 'privilege' and 'privilege' in native:
            native['privileges'] = [copy.deepcopy(native['privilege'])]
        elif kind in {
            'deployment', 'replica-set', 'shard', 'router', 'zone',
            'balancer',
        }:
            native['state'] = copy.deepcopy(native)
        identifier = ':'.join([
            'mongodb', kind,
            *(self._part(item) for item in path),
            self._part(name),
        ])
        return {
            'resource_id': identifier,
            'resource_kind': kind,
            'display_name': str(name),
            'display_path': ['MongoDB', *path, str(name)],
            'authority_path': ['mongodb', kind, *path, str(name)],
            'generation': generation,
            'is_virtual': virtual,
            'native': native,
        }

    def list_resources(self, request):
        client, route = self._connect(request)
        try:
            build = client.admin.command('buildInfo')
            hello = client.admin.command('hello')
            is_router = hello.get('msg') == 'isdbgrid'
            topology_catalog = {}
            if is_router:
                try:
                    topology_catalog = {
                        'shards': list(client.config.shards.find({}, {
                            '_id': 1, 'host': 1, 'state': 1,
                            'draining': 1, 'tags': 1,
                        })),
                        'zones': list(client.config.tags.find({}, {
                            'ns': 1, 'min': 1, 'max': 1, 'tag': 1,
                        })),
                    }
                except Exception:
                    topology_catalog = {'authorization_filtered': True}
            generation = self._generation(
                build, hello, topology_catalog
            )
            deployment_name = f"{route['host']}:{route.get('port', 27017)}"
            resources = [self._resource(
                'deployment', [], deployment_name, generation,
                {'database': 'admin', 'hello': hello},
            )]
            resources.extend((
                self._resource(
                    'current-operation', [], 'Current operations',
                    generation, {'database': 'admin'}, virtual=True,
                ),
                self._resource(
                    'server-log', [], 'Server log', generation,
                    {'database': 'admin', 'log': 'global'}, virtual=True,
                ),
            ))
            set_name = hello.get('setName')
            if set_name:
                resources.append(self._resource(
                    'replica-set', [], set_name, generation,
                    {'database': 'admin', 'set_name': set_name},
                ))
            if is_router:
                resources.append(self._resource(
                    'router', [], deployment_name, generation,
                    {'database': 'admin', 'host': deployment_name},
                ))

            database_names = client.list_database_names()[:MAX_DATABASES]
            # The route database is the session/query default and auth source,
            # not a resource-explorer filter.  An explicit discovery request
            # may narrow the catalog without hiding other databases in the UI.
            selected = request.get('database')
            if selected:
                selected = _identifier(selected, 'database')
                database_names = [selected]
            collection_count = 0
            index_count = 0
            for database_name in database_names:
                resources.append(self._resource(
                    'database', [], database_name, generation,
                    {'database': database_name},
                ))
                database = client[database_name]
                try:
                    profile = database.command({'profile': -1})
                except Exception:
                    profile = {}
                resources.append(self._resource(
                    'profiling', [database_name], 'Profiler', generation,
                    {'database': database_name, 'profile': profile},
                    virtual=True,
                ))
                try:
                    stats = database.command({'dbStats': 1, 'scale': 1})
                except Exception:
                    stats = {}
                resources.append(self._resource(
                    'statistics', [database_name], 'Database statistics',
                    generation, {'database': database_name, 'stats': stats},
                    virtual=True,
                ))
                try:
                    collections = list(database.list_collections())
                except Exception:
                    collections = []
                for info in collections:
                    if collection_count >= MAX_COLLECTIONS:
                        break
                    name = str(info['name'])
                    kind = (
                        'view' if info.get('type') == 'view'
                        else 'collection'
                    )
                    native = {
                        'database': database_name,
                        'collection': name,
                        'type': info.get('type'),
                        'options': info.get('options', {}),
                    }
                    collection_resource = self._resource(
                        kind, [database_name], name, generation, native,
                    )
                    resources.append(collection_resource)
                    collection_count += 1
                    if kind == 'collection':
                        resources.append(self._resource(
                            'aggregation-pipeline', [database_name, name],
                            'Aggregation pipeline', generation, native,
                            virtual=True,
                        ))
                    validator = info.get('options', {}).get('validator')
                    if validator:
                        resources.append(self._resource(
                            'validator', [database_name, name], 'validator',
                            generation, {**native, 'validator': validator},
                        ))
                    if kind == 'collection':
                        try:
                            indexes = list(database[name].list_indexes())
                        except Exception:
                            indexes = None
                        if indexes is not None:
                            collection_resource['native']['indexes'] = []
                        else:
                            indexes = ()
                        for index in indexes:
                            if index_count >= MAX_INDEXES:
                                break
                            index_value = dict(index)
                            index_name = str(index_value.pop('name'))
                            index_resource = self._resource(
                                'index', [database_name, name], index_name,
                                generation, {
                                    **native, 'index': index_value,
                                    'index_name': index_name,
                                },
                            )
                            resources.append(index_resource)
                            collection_resource['native']['indexes'].append({
                                'resource_id': index_resource['resource_id'],
                                'name': index_name,
                                'definition': copy.deepcopy(index_value),
                            })
                            index_count += 1
                        if set_name or is_router:
                            resources.append(self._resource(
                                'change-stream', [database_name, name],
                                'watch', generation, native, virtual=True,
                            ))
                    if (
                        request.get('include_documents') and
                        request.get('collection') == name
                    ):
                        for document in database[name].find({}).limit(
                            MAX_RESOURCE_DOCUMENTS
                        ):
                            document_id = document.get('_id')
                            resources.append(self._resource(
                                'document', [database_name, name],
                                str(document_id), generation,
                                {**native, 'document': document},
                            ))
            security_databases = list(dict.fromkeys([
                *database_names, 'admin',
            ]))
            self._append_security_resources(
                resources, client, security_databases, generation
            )
            if is_router:
                self._append_sharding_resources(
                    resources, client, generation
                )
            tools = self._tool_runner.available()
            for tool_kind, label, executable in (
                ('backup', 'Backup', 'mongodump'),
                ('restore', 'Restore', 'mongorestore'),
                ('import', 'Import', 'mongoimport'),
                ('export', 'Export', 'mongoexport'),
                ('shell', 'MongoDB Shell', 'mongosh'),
            ):
                resources.append(self._resource(
                    tool_kind, [], label, generation,
                    {
                        'database': route.get('database') or 'admin',
                        'executable_id': executable,
                        'available': tools[executable],
                        'workspace_granted': bool(
                            route.get('tool_workspace')
                        ),
                    },
                    virtual=True,
                ))
            return resources
        except MongoDBClientError:
            raise
        except Exception as exc:
            raise MongoDBClientError(
                f'MongoDB resource discovery failed ({type(exc).__name__})'
            ) from None
        finally:
            self._forget_client(client)

    def _append_security_resources(self, resources, client, database_names,
                                   generation):
        for database_name in database_names:
            try:
                users = client[database_name].command({'usersInfo': 1})
                roles = client[database_name].command({
                    'rolesInfo': 1, 'showPrivileges': True,
                })
            except Exception:
                continue
            for user in users.get('users', []):
                name = str(user.get('user'))
                resources.append(self._resource(
                    'user', [database_name], name, generation,
                    {'database': database_name, 'user': name,
                     'roles': user.get('roles', [])},
                ))
            for role in roles.get('roles', []):
                name = str(role.get('role'))
                native = {
                    'database': database_name, 'role': name,
                    'roles': role.get('roles', []),
                    'privileges': role.get('privileges', []),
                }
                resources.append(self._resource(
                    'role', [database_name], name, generation, native,
                ))
                for offset, privilege in enumerate(
                    role.get('privileges', [])
                ):
                    resources.append(self._resource(
                        'privilege', [database_name, name], str(offset),
                        generation, {**native, 'privilege': privilege},
                    ))

    def _append_sharding_resources(self, resources, client, generation):
        try:
            config = client['config']
            for shard in config['shards'].find({}):
                name = str(shard.get('_id'))
                resources.append(self._resource(
                    'shard', [], name, generation,
                    {'database': 'config', 'shard': shard},
                ))
            for zone in config['tags'].find({}):
                name = f"{zone.get('ns')}:{zone.get('tag')}"
                resources.append(self._resource(
                    'zone', [], name, generation,
                    {'database': 'config', 'zone': zone},
                ))
            state = client.admin.command({'balancerStatus': 1})
            resources.append(self._resource(
                'balancer', [], 'balancer', generation,
                {'database': 'admin', 'status': state}, virtual=True,
            ))
        except Exception:
            return

    def inspect_resource(self, request):
        resource_id = request.get('resource_id')
        for resource in self.list_resources(request):
            if resource.get('resource_id') == resource_id:
                return resource
        raise MongoDBClientError('MongoDB resource is unavailable')

    def describe_security(self, request):
        client, _route = self._connect(request)
        try:
            status = client.admin.command({
                'connectionStatus': 1, 'showPrivileges': True,
            })
            build = client.admin.command('buildInfo')
            auth = status.get('authInfo', {})
            return {
                'resource_id': 'mongodb:security:current',
                'display_name': 'Current MongoDB authorization',
                'authority_path': ['mongodb', 'security', 'current'],
                'generation': str(
                    build.get('gitVersion') or build.get('version')
                ),
                'native': self._extended_json({
                    'authenticated_users': auth.get(
                        'authenticatedUsers', []
                    ),
                    'authenticated_roles': auth.get(
                        'authenticatedUserRoles', []
                    ),
                    'authenticated_privileges': auth.get(
                        'authenticatedUserPrivileges', []
                    ),
                }),
            }
        except Exception as exc:
            raise MongoDBClientError(
                f'MongoDB security discovery failed ({type(exc).__name__})'
            ) from None
        finally:
            self._forget_client(client)

    ADMIN_OPERATIONS = {
        'deployment': frozenset({'inspect', 'execute'}),
        'replica-set': frozenset({'inspect', 'alter', 'execute'}),
        'shard': frozenset({'inspect', 'execute'}),
        'router': frozenset({'inspect', 'execute'}),
        'database': frozenset({'inspect', 'drop'}),
        'collection': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
            'insert', 'update', 'delete',
        }),
        'view': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'document': frozenset({'inspect', 'insert', 'update', 'delete'}),
        'index': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'validator': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'user': frozenset({
            'inspect', 'create', 'alter', 'grant', 'revoke', 'drop',
        }),
        'role': frozenset({
            'inspect', 'create', 'alter', 'grant', 'revoke', 'drop',
        }),
        'privilege': frozenset({'inspect'}),
        'zone': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'balancer': frozenset({'inspect', 'execute'}),
        'change-stream': frozenset({'inspect', 'execute'}),
        'aggregation-pipeline': frozenset({'inspect', 'execute'}),
        'profiling': frozenset({'inspect', 'execute'}),
        'current-operation': frozenset({'inspect', 'execute'}),
        'server-log': frozenset({'inspect'}),
        'statistics': frozenset({'inspect'}),
        'backup': frozenset({'inspect', 'execute'}),
        'restore': frozenset({'inspect', 'execute'}),
        'import': frozenset({'inspect', 'execute'}),
        'export': frozenset({'inspect', 'execute'}),
        'shell': frozenset({'inspect', 'execute'}),
    }

    def supports_admin_operation(self, resource_kind, operation_id):
        return operation_id in self.ADMIN_OPERATIONS.get(
            resource_kind, frozenset()
        )

    def visual_admin_catalog(self, catalog):
        catalog['native_planner'] = 'pymongo-command-planner'
        catalog['query_language'] = 'mongodb-query-api-json'
        catalog['document_encoding'] = 'mongodb-canonical-extended-json'
        catalog['experience_families'] = ['document']

        def declaration(*resource_kinds):
            return {
                'status': 'supported',
                'resource_kinds': list(resource_kinds),
                'operation_obligations': {
                    kind: sorted(self.ADMIN_OPERATIONS[kind])
                    for kind in resource_kinds
                },
                'reason': (
                    'MongoDB objects and operations are discovered and '
                    'executed through the native PyMongo provider.'
                ),
                'evidence': ['mongodb-native-query-and-admin-api'],
            }

        catalog['concept_declarations'] = {'document': {
            'databases': declaration('database'),
            'collections': declaration('collection'),
            'documents': declaration('document'),
            'validation_rules': declaration('validator'),
            'indexes': declaration('index'),
            'views': declaration('view'),
            'aggregation_pipelines': declaration('aggregation-pipeline'),
            'users_and_roles': declaration('user', 'role', 'privilege'),
            'replica_sets_and_sharding': declaration(
                'replica-set', 'shard', 'router'
            ),
        }}
        # These operations create a child of a selected native container.
        # The generic catalog cannot express that relationship itself.
        target_kinds = {
            ('document', 'insert'): ['collection'],
            ('document', 'update'): ['collection'],
            ('document', 'delete'): ['collection'],
            ('index', 'create'): ['collection'],
            ('validator', 'create'): ['collection'],
        }
        custom_profiles = {
            'zone': 'namespace',
        }
        for resource in catalog.get('objects', []):
            if resource.get('resource_kind') in custom_profiles:
                # The portfolio expands this entry with topology operations;
                # the provider additionally admits create/drop through its
                # own catalog so zones can be managed without raw commands.
                resource['operations'] = self._zone_operations(
                    resource.get('operations', [])
                )
            for operation in resource.get('operations', []):
                native_form = self._admin_form(
                    resource.get('resource_kind'),
                    operation.get('operation_id'),
                )
                if native_form is not None:
                    operation['form'] = native_form
                key = (
                    resource.get('resource_kind'),
                    operation.get('operation_id'),
                )
                if key in target_kinds:
                    operation['target_required'] = True
                    operation['target_resource_kinds'] = target_kinds[key]
                if resource.get('resource_kind') in {
                    'backup', 'restore', 'import', 'export', 'shell',
                }:
                    operation['required_permissions'] = [
                        'filesystem', 'execute',
                    ]
                if (
                    resource.get('resource_kind') == 'aggregation-pipeline'
                    and operation.get('operation_id') == 'execute'
                ):
                    operation.update({
                        'title': 'Run aggregation pipeline',
                        'mutation_class': 'read',
                        'confirmation_required': False,
                        'form': {
                            'form_id': 'mongodb-aggregation-pipeline',
                            'title': 'Run aggregation pipeline',
                            'fields': [{
                                'field_id': 'pipeline',
                                'label': 'Pipeline stages',
                                'control': 'json', 'required': True,
                                'json_type': 'array', 'default': [],
                            }, {
                                'field_id': 'options',
                                'label': 'Execution options',
                                'control': 'json', 'required': False,
                                'json_type': 'object', 'default': {},
                            }, {
                                'field_id': 'max_documents',
                                'label': 'Maximum result documents',
                                'control': 'number', 'required': False,
                                'default': 100, 'minimum': 1,
                                'maximum': MAX_RESULT_DOCUMENTS,
                            }],
                        },
                    })
                if resource.get('resource_kind') == 'document':
                    form = self._document_admin_form(
                        operation.get('operation_id')
                    )
                    if form is not None:
                        operation['form'] = form
        return catalog

    @staticmethod
    def _field(field_id, label, control='text', required=False, **values):
        return {
            'field_id': field_id, 'label': label, 'control': control,
            'required': required, **values,
        }

    @classmethod
    def _admin_form(cls, kind, operation):
        """Return the form admitted by the MongoDB native operation path."""
        f = cls._field
        if operation == 'inspect':
            return {
                'form_id': f'mongodb.{kind}.inspect.v1',
                'title': f'Inspect MongoDB {kind.replace("-", " ")}',
                'fields': [],
            }
        if operation == 'drop':
            return {
                'form_id': f'mongodb.{kind}.drop.v1',
                'title': f'Drop MongoDB {kind.replace("-", " ")}',
                'fields': [f(
                    'confirmation',
                    f'Type the selected {kind.replace("-", " ")} name to '
                    'confirm', required=True,
                )],
            }
        if kind in {'collection', 'view'}:
            if operation == 'create':
                label = 'view' if kind == 'view' else 'collection'
                return {
                    'form_id': f'mongodb.{kind}.create.v1',
                    'title': f'Create MongoDB {label}',
                    'fields': [
                        f('name', f'{label.title()} name', required=True),
                        f('database', 'Database',
                          initial_value_path=['database'],
                          help='Uses the selected database. Enter a database '
                               'when creating from a server connection.'),
                        *cls._collation_fields(kind),
                        *([
                            f('view_source', 'Source collection or view'),
                            f('configure_pipeline', 'Define view pipeline',
                              'boolean', default=False),
                            f('view_pipeline', 'View aggregation pipeline',
                              'json', default=[], json_type='array',
                              visible_when={'field_id': 'configure_pipeline',
                                            'equals': True}),
                        ] if kind == 'view' else []),
                        f(
                            'options', f'MongoDB {label} options', 'json',
                            False, default={},
                            json_type='object',
                        ),
                    ],
                }
            if operation == 'alter':
                return {
                    'form_id': f'mongodb.{kind}.alter.v1',
                    'title': f'Modify MongoDB {kind}',
                    'fields': [f(
                        'changes', 'collMod command fields', 'json', True,
                        default={}, json_type='object',
                    )],
                }
            if operation == 'rename' and kind == 'collection':
                return {
                    'form_id': 'mongodb.collection.rename.v1',
                    'title': 'Rename MongoDB collection',
                    'fields': [f(
                        'new_name', 'New collection name', required=True,
                    )],
                }
            if kind == 'collection' and operation in {
                    'insert', 'update', 'delete'}:
                return cls._document_admin_form(operation)
        if kind == 'index':
            if operation == 'create':
                return {
                    'form_id': 'mongodb.index.create.v1',
                    'title': 'Create MongoDB index',
                    'fields': [
                        f('name', 'Index name', required=True),
                        f('keys', 'Ordered index keys', 'json', default=[],
                          json_type='array', array_editor={
                              'item_kind': 'object', 'fields': [
                                  f('field', 'Document field path',
                                    required=True),
                                  f('kind', 'Key type', 'select', True,
                                    default='ascending', options=[
                                        {'value': value, 'label': label}
                                        for value, label in (
                                            ('ascending', 'Ascending'),
                                            ('descending', 'Descending'),
                                            ('text', 'Text'),
                                            ('hashed', 'Hashed'),
                                            ('2d', '2D geospatial'),
                                            ('2dsphere', '2D sphere'),
                                        )]),
                              ]}),
                        *[f(field + '_mode', label, 'select',
                            default='native', options=[
                                {'value': 'native',
                                 'label': 'Native default / advanced options'},
                                {'value': 'enabled', 'label': 'Enabled'},
                                {'value': 'disabled', 'label': 'Disabled'},
                            ]) for field, label in (
                                ('unique', 'Enforce unique keys'),
                                ('sparse', 'Sparse index'),
                                ('hidden', 'Hide from query planner'),
                            )],
                        f('enable_ttl', 'Enable automatic expiration (TTL)',
                          'boolean', default=False),
                        f('ttl_seconds', 'Expire after seconds', 'number',
                          True, minimum=0, maximum=2147483647,
                          visible_when={'field_id': 'enable_ttl',
                                        'equals': True}),
                        f('configure_text', 'Configure text index options',
                          'boolean', default=False),
                        f('text_weights', 'Text field weights', 'json',
                          default=[], json_type='array',
                          visible_when={'field_id': 'configure_text',
                                        'equals': True},
                          array_editor={'item_kind': 'object', 'fields': [
                              f('field', 'Weighted document field',
                                required=True),
                              f('weight', 'Weight', 'number', True, default=1,
                                minimum=1, maximum=99999),
                          ]}),
                        *[f(field, label,
                            visible_when={'field_id': 'configure_text',
                                          'equals': True})
                          for field, label in (
                              ('text_language',
                               'Default language (blank uses native default)'),
                              ('text_language_override',
                               'Language field (blank uses native default)'),
                          )],
                        f('text_version', 'Text index version', 'select',
                          default='native',
                          visible_when={'field_id': 'configure_text',
                                        'equals': True},
                          options=[{'value': v, 'label': label}
                                   for v, label in (
                                       ('native', 'Native default'),
                                       ('2', '2'), ('3', '3'))]),
                        *cls._collation_fields(),
                        f('geo_mode', 'Geospatial index options', 'select',
                          default='native', options=[
                              {'value': value, 'label': label}
                              for value, label in (
                                  ('native', 'Native / advanced options'),
                                  ('2d', 'Planar 2D'),
                                  ('2dsphere', 'Spherical 2D'))]),
                        f('geo_bits', '2D precision bits (blank uses native)',
                          'number', minimum=1, maximum=32,
                          visible_when={'field_id': 'geo_mode',
                                        'equals': '2d'}),
                        *[f('geo_' + key, label, 'number',
                            visible_when={'field_id': 'geo_mode',
                                          'equals': '2d'}) for key, label in (
                              ('min', 'Minimum (blank uses native)'),
                              ('max', 'Maximum (blank uses native)'),
                          )],
                        f('geo_version', '2dsphere index version', 'select',
                          default='native',
                          visible_when={'field_id': 'geo_mode',
                                        'equals': '2dsphere'},
                          options=[{'value': v, 'label': v} for v in (
                              'native', '1', '2', '3')]),
                        f('configure_wildcard',
                          'Configure wildcard projection',
                          'boolean', default=False),
                        f('wildcard_fields', 'Wildcard projection fields',
                          'json', True, default=[], json_type='array',
                          visible_when={'field_id': 'configure_wildcard',
                                        'equals': True},
                          array_editor={'item_kind': 'object', 'fields': [
                              f('field', 'Document field path', required=True),
                              f('action', 'Projection action', 'select', True,
                                default='include', options=[
                                    {'value': 'include', 'label': 'Include'},
                                    {'value': 'exclude', 'label': 'Exclude'},
                                ]),
                          ]}),
                        f(
                            'options', 'Additional native index options',
                            'json', default={}, json_type='object',
                        ),
                    ],
                }
            if operation == 'alter':
                return {
                    'form_id': 'mongodb.index.alter.v1',
                    'title': 'Modify MongoDB index properties',
                    'fields': [
                        f('visibility', 'Index visibility', 'select',
                          default='unchanged', options=[
                              {'value': value, 'label': label} for value, label
                              in (('unchanged', 'Unchanged'),
                                  ('hidden', 'Hidden'),
                                  ('visible', 'Visible'))
                          ]),
                        f('change_ttl', 'Change TTL', 'boolean',
                          default=False),
                        f('ttl_seconds', 'Expire after seconds',
                          'number', True,
                          initial_value_path=['editor_values', 'ttl_seconds'],
                          minimum=0, maximum=2147483647,
                          visible_when={'field_id': 'change_ttl',
                                        'equals': True}),
                        f('uniqueness', 'Uniqueness operation', 'select',
                          default='unchanged', options=[
                              {'value': value, 'label': label} for value, label
                              in (('unchanged', 'Unchanged'),
                                  ('prepare', 'Prepare unique conversion'),
                                  ('cancel_prepare', 'Cancel preparation'),
                                  ('unique', 'Convert to unique'),
                                  ('non_unique', 'Convert to non-unique'))
                          ]),
                        f('changes', 'Additional native index changes',
                          'json', default={}, json_type='object'),
                    ],
                }
        if kind == 'validator' and operation in {'create', 'alter'}:
            container = 'options' if operation == 'create' else 'changes'
            return {
                'form_id': f'mongodb.validator.{operation}.v1',
                'title': f'{operation.title()} MongoDB validation rule',
                'fields': [
                    f('replace_rule', 'Replace validation rule', 'boolean',
                      default=False),
                    f('validator', 'Validation rule (empty object clears it)',
                      'json', default={}, json_type='object',
                      initial_value_path=['options', 'validator'],
                      submit_unchanged=True,
                      visible_when={'field_id': 'replace_rule',
                                    'equals': True}),
                    f('validation_level', 'Validation level', 'select',
                      initial_value_path=['options', 'validationLevel'],
                      default='unchanged', options=[
                          {'value': v, 'label': v.title()}
                          for v in ('unchanged', 'off', 'strict', 'moderate')
                      ]),
                    f('validation_action', 'Validation action', 'select',
                      initial_value_path=['options', 'validationAction'],
                      default='unchanged', options=[
                          {'value': v, 'label': label} for v, label in (
                              ('unchanged', 'Unchanged'), ('error', 'Reject'),
                              ('warn', 'Warn only'),
                              ('errorAndLog', 'Reject and log'))]),
                    f(container, 'Native validation changes', 'json',
                      default={}, json_type='object'),
                ],
            }
        if kind in {'user', 'role'}:
            if operation in {'create', 'alter'}:
                container = 'options' if operation == 'create' else 'changes'
                fields = [] if operation == 'alter' else [
                    f('name', f'{kind.title()} name', required=True),
                ]
                default = {'database': 'admin', 'roles': []}
                if kind == 'role':
                    default['privileges'] = []
                else:
                    default['credential_reference_id'] = ''
                fields.append(f(
                    container,
                    f'MongoDB {kind} {operation} fields', 'json', True,
                    default=default, json_type='object',
                ))
                return {
                    'form_id': f'mongodb.{kind}.{operation}.v1',
                    'title': f'{operation.title()} MongoDB {kind}',
                    'fields': fields,
                }
            if operation in {'grant', 'revoke'}:
                return {
                    'form_id': f'mongodb.{kind}.{operation}.v1',
                    'title': f'{operation.title()} MongoDB {kind} rights',
                    'fields': [f(
                        'privileges',
                        'Roles' if kind == 'user' else 'Privileges',
                        'json', True, default=[], json_type='array',
                    )],
                }
        action_options = {
            'deployment': (
                'add_shard', 'remove_shard', 'enable_sharding',
                'shard_collection', 'reshard_collection',
                'unshard_collection', 'move_collection', 'move_primary',
            ),
            'shard': (
                'add_shard', 'remove_shard', 'enable_sharding',
                'shard_collection', 'reshard_collection',
                'unshard_collection', 'move_collection', 'move_primary',
            ),
            'replica-set': (
                'reconfigure', 'step_down', 'freeze', 'sync_from',
            ),
            'router': ('flush_configuration',),
            'balancer': ('start', 'stop', 'status', 'collection_status'),
            'profiling': ('set',),
            'current-operation': ('kill',),
            'change-stream': ('open',),
        }
        if kind == 'replica-set' and operation == 'alter':
            return {
                'form_id': 'mongodb.replica-set.alter.v1',
                'title': 'Reconfigure MongoDB replica set',
                'fields': [f(
                    'changes', 'Replica-set configuration and force option',
                    'json', True, default={'config': {}, 'force': False},
                    json_type='object',
                )],
            }
        if operation == 'execute' and kind in action_options:
            return {
                'form_id': f'mongodb.{kind}.execute.v1',
                'title': f'Run MongoDB {kind.replace("-", " ")} action',
                'fields': [
                    f(
                        'action', 'MongoDB action', 'select', True,
                        options=[{'value': value,
                                  'label': value.replace('_', ' ').title()}
                                 for value in action_options[kind]],
                    ),
                    f(
                        'arguments', 'Action arguments', 'json', False,
                        default={}, json_type='object',
                    ),
                    f('confirmation', 'Confirm administrative action',
                      required=True),
                ],
            }
        if kind in {'backup', 'restore', 'import', 'export', 'shell'} and (
                operation == 'execute'):
            choices = {
                'backup': ('archive',), 'restore': ('archive',),
                'import': ('extended_json', 'json_lines', 'json', 'csv'),
                'export': ('extended_json', 'json_lines', 'json', 'csv'),
                'shell': ('script',),
            }[kind]
            return {
                'form_id': f'mongodb.{kind}.execute.v1',
                'title': f'Run MongoDB {kind} task',
                'fields': [
                    f(
                        'action', 'Tool action', 'select', True,
                        options=[{'value': value,
                                  'label': value.replace('_', ' ').title()}
                                 for value in choices],
                    ),
                    f(
                        'arguments', 'MongoDB tool arguments', 'json', True,
                        default={}, json_type='object',
                    ),
                    f('confirmation', 'Confirm external tool execution',
                      required=True),
                ],
            }
        return None

    @staticmethod
    def _document_admin_form(operation_id):
        """Return MongoDB-native single-document task forms."""
        forms = {
            'inspect': {
                'form_id': 'mongodb.document.inspect.v1',
                'title': 'Inspect MongoDB document',
                'fields': [],
            },
            'insert': {
                'form_id': 'mongodb.document.insert.v1',
                'title': 'Insert MongoDB document',
                'fields': [{
                    'field_id': 'values',
                    'label': 'Document (MongoDB Extended JSON)',
                    'control': 'json', 'required': True,
                    'json_type': 'object', 'default': {},
                }, {
                    'field_id': 'options',
                    'label': 'Insert options',
                    'control': 'json', 'required': False,
                    'json_type': 'object', 'default': {},
                }],
            },
            'update': {
                'form_id': 'mongodb.document.update.v1',
                'title': 'Update or replace MongoDB document',
                'fields': [{
                    'field_id': 'selector',
                    'label': 'MongoDB document selector',
                    'control': 'json', 'required': True,
                    'json_type': 'object', 'default': {},
                }, {
                    'field_id': 'changes',
                    'label': 'Update operators or replacement document',
                    'control': 'json', 'required': True,
                    'json_type': 'object', 'default': {},
                }, {
                    'field_id': 'concurrency_token',
                    'label': 'CDEadmin document concurrency token',
                    'control': 'text', 'required': False,
                }],
            },
            'delete': {
                'form_id': 'mongodb.document.delete.v1',
                'title': 'Delete MongoDB document',
                'fields': [{
                    'field_id': 'selector',
                    'label': 'MongoDB document selector',
                    'control': 'json', 'required': True,
                    'json_type': 'object', 'default': {},
                }, {
                    'field_id': 'concurrency_token',
                    'label': 'CDEadmin document concurrency token',
                    'control': 'text', 'required': False,
                }, {
                    'field_id': 'confirmation',
                    'label': 'Confirm single-document deletion',
                    'control': 'text', 'required': True,
                }],
            },
        }
        return copy.deepcopy(forms.get(operation_id))

    @staticmethod
    def _zone_operations(existing):
        by_id = {item['operation_id']: item for item in existing}
        inspect = copy.deepcopy(by_id['inspect'])
        alter = copy.deepcopy(by_id['alter'])
        alter['form'] = {
            'form_id': 'mongodb.zone.alter.v1',
            'title': 'Alter MongoDB zone mapping',
            'fields': [{
                'field_id': 'changes',
                'label': 'Zone range or shard-assignment changes',
                'control': 'json', 'required': True,
                'json_type': 'object', 'default': {},
            }],
        }
        create = copy.deepcopy(alter)
        create.update({
            'operation_id': 'create', 'title': 'Create zone mapping',
            'target_required': False, 'confirmation_required': True,
        })
        create['form'] = {
            'form_id': 'zone-create', 'title': 'Create zone mapping',
            'fields': [{
                'field_id': 'options', 'label': 'Zone mapping',
                'control': 'json', 'required': True,
            }],
        }
        drop = copy.deepcopy(alter)
        drop.update({
            'operation_id': 'drop', 'title': 'Remove zone mapping',
            'mutation_class': 'destructive', 'target_required': True,
            'confirmation_required': True,
        })
        drop['form'] = {
            'form_id': 'zone-drop', 'title': 'Remove zone mapping',
            'fields': [{
                'field_id': 'confirmation', 'label': 'Confirmation',
                'control': 'text', 'required': True,
            }],
        }
        return [inspect, create, alter, drop]

    @staticmethod
    def _native_target(target):
        target = _mapping(target, 'target resource')
        native = target.get('native')
        if isinstance(native, Mapping):
            return copy.deepcopy(dict(native))
        extensions = target.get('extensions', {})
        if isinstance(extensions, Mapping):
            mongodb = extensions.get('mongodb', {})
            if isinstance(mongodb, Mapping) and isinstance(
                mongodb.get('native'), Mapping
            ):
                return copy.deepcopy(dict(mongodb['native']))
        raise MongoDBClientError(
            'MongoDB target has no provider-native identity'
        )

    def validate_admin_operation(self, request):
        kind = request['resource_kind']
        operation = request['operation_id']
        draft = request.get('draft', {})
        errors = []
        if kind in {'collection', 'view'} and operation == 'create':
            try:
                if kind == 'view':
                    self._view_creation_options(draft)
                else:
                    self._collection_options(draft)
            except MongoDBClientError as error:
                errors.append({'field_id': 'options', 'code': 'options',
                               'message': str(error)})
        if kind == 'validator' and operation in {'create', 'alter'}:
            try:
                self._validator_changes(operation, draft)
            except MongoDBClientError as error:
                errors.append({'field_id': 'validator', 'code': 'validator',
                               'message': str(error)})
        if kind == 'index' and operation == 'alter':
            try:
                self._index_changes(draft)
            except MongoDBClientError as error:
                errors.append({'field_id': 'changes', 'code': 'index_changes',
                               'message': str(error)})
        if kind == 'index' and operation == 'create':
            try:
                self._index_options(draft)
            except MongoDBClientError as error:
                errors.append({'field_id': 'keys', 'code': 'index_keys',
                               'message': str(error)})
        if kind == 'aggregation-pipeline' and operation == 'execute':
            pipeline = draft.get('pipeline')
            if not isinstance(pipeline, list) or any(
                    not isinstance(stage, Mapping) for stage in pipeline):
                errors.append({
                    'field_id': 'pipeline', 'code': 'type',
                    'message': 'Aggregation pipeline must be an array of '
                               'stage objects.',
                })
            elif any(
                set(stage).intersection({'$out', '$merge'})
                for stage in pipeline
            ):
                errors.append({
                    'field_id': 'pipeline', 'code': 'mutation_stage',
                    'message': 'Read-only aggregation cannot use $out or '
                               '$merge.',
                })
        if kind in {'collection', 'view'} and operation == 'create':
            try:
                self._creation_database(request, draft)
            except MongoDBClientError as error:
                errors.append({
                    'field_id': 'database', 'code': 'database',
                    'message': str(error),
                })
        if kind in {'user', 'role'} and operation == 'create':
            options = draft.get('options', {})
            if not isinstance(options, Mapping) or not isinstance(
                options.get('database'), str
            ):
                errors.append({
                    'field_id': 'options', 'code': 'database_required',
                    'message': 'MongoDB create options require database.',
                })
        if kind == 'user' and operation == 'create':
            options = draft.get('options', {})
            if not isinstance(options, Mapping) or not isinstance(
                options.get('credential_reference_id'), str
            ):
                errors.append({
                    'field_id': 'options',
                    'code': 'credential_reference_required',
                    'message': 'MongoDB user creation requires a secret '
                               'reference.',
                })
        if kind == 'document' and operation == 'update':
            changes = draft.get('changes', {})
            if isinstance(changes, Mapping) and (
                '_id' in changes or
                isinstance(changes.get('$set'), Mapping) and
                '_id' in changes['$set'] or
                isinstance(changes.get('$unset'), Mapping) and
                '_id' in changes['$unset']
            ):
                errors.append({
                    'field_id': 'changes', 'code': 'immutable_field',
                    'message': 'MongoDB _id is immutable and cannot be '
                               'changed or unset.',
                })
        if kind == 'document' and operation in {'insert', 'update'}:
            target = request.get('target_resource')
            try:
                native = self._native_target(target)
            except MongoDBClientError:
                native = {}
            validator = native.get('options', {}).get('validator', {})
            schema = validator.get('$jsonSchema', {}) if isinstance(
                validator, Mapping
            ) else {}
            document = draft.get('values') if operation == 'insert' else (
                draft.get('changes')
            )
            replacement = isinstance(document, Mapping) and not any(
                str(key).startswith('$') for key in document
            )
            if isinstance(schema, Mapping) and replacement:
                errors.extend(self._json_schema_diagnostics(schema, document))
        return {'errors': errors}

    @staticmethod
    def _json_schema_diagnostics(schema, document):
        """Return bounded hints without replacing server validation."""
        errors = []
        required = schema.get('required', [])
        if isinstance(required, list):
            for name in required:
                if name not in document:
                    errors.append({
                        'field_id': 'values', 'code': 'schema_required',
                        'message': f'Required field {name!r} is missing.',
                    })
        properties = schema.get('properties', {})
        if not isinstance(properties, Mapping):
            return errors
        bson_types = {
            'object': Mapping, 'array': list, 'string': str, 'bool': bool,
            'boolean': bool, 'int': int, 'long': int,
            'double': (int, float), 'number': (int, float), 'null': type(None),
        }
        for name, rule in properties.items():
            if name not in document or not isinstance(rule, Mapping):
                continue
            expected = rule.get('bsonType', rule.get('type'))
            if isinstance(expected, list):
                admitted = tuple(
                    bson_types[item] for item in expected
                    if item in bson_types
                )
            else:
                admitted = bson_types.get(expected)
            if admitted and not isinstance(document[name], admitted):
                errors.append({
                    'field_id': 'values', 'code': 'schema_type',
                    'message': f'Field {name!r} does not match {expected!r}.',
                })
        return errors[:100]

    def plan_admin_operation(self, request):
        kind = request['resource_kind']
        operation = request['operation_id']
        draft = copy.deepcopy(request.get('draft', {}))
        if kind in {'collection', 'view'} and operation == 'create':
            database_name = self._creation_database(request, draft)
            draft = {'name': draft.get('name'),
                     'options': (self._view_creation_options(draft)
                                 if kind == 'view' else
                                 self._collection_options(draft))}
            draft['options']['database'] = database_name
        if kind == 'validator' and operation in {'create', 'alter'}:
            container = 'options' if operation == 'create' else 'changes'
            draft = {container: self._validator_changes(operation, draft)}
        if kind == 'index' and operation == 'alter':
            draft = {'changes': self._index_changes(draft)}
        if kind == 'index' and operation == 'create':
            draft = {'name': draft.get('name'),
                     'options': self._index_options(draft)}
        target = request.get('target_resource')
        native = self._native_target(target) if target else {}
        provider_payload = {
            'resource_kind': kind,
            'operation_id': operation,
            'draft': draft,
            'native': native,
            '_provider_route': copy.deepcopy(request.get('_provider_route')),
        }
        preview_draft = copy.deepcopy(draft)
        warnings = []
        if kind == 'index' and operation == 'create' and (
                'expireAfterSeconds' in draft['options']):
            warnings.append(
                'TTL indexes automatically delete expired documents. '
                'Review retention before applying, including existing data.')
        if kind == 'validator' and operation in {'create', 'alter'}:
            changes = draft.get('changes', draft.get('options', {}))
            if changes.get('validationLevel') == 'off' or (
                    changes.get('validationAction') == 'warn') or (
                    'validator' in changes and not changes['validator']):
                warnings.append(
                    'This change relaxes validation or clears the rule. '
                    'Review whether invalid documents should be accepted.')
        if kind == 'index' and operation == 'alter':
            changes = draft['changes']
            if 'expireAfterSeconds' in changes:
                warnings.append(
                    'TTL changes can make existing documents eligible for '
                    'automatic deletion. Review retention before applying.')
            if changes.get('prepareUnique'):
                warnings.append(
                    'Unique preparation rejects new duplicate keys; existing '
                    'duplicates must be resolved before conversion.')
            if changes.get('forceNonUnique'):
                warnings.append(
                    'Non-unique conversion removes uniqueness enforcement.')
        preview_observations = {}
        for container in ('options', 'changes'):
            value = preview_draft.get(container)
            if isinstance(value, Mapping) and (
                'credential_reference_id' in value
            ):
                preview_draft[container] = {
                    **value, 'credential_reference_id': '<secret-reference>',
                }
        if kind in {'document', 'collection'} and operation in {
            'update', 'delete'
        }:
            selector = draft.get('selector', {})
            if isinstance(selector, Mapping):
                route = request.get('_provider_route')
                client = None
                try:
                    client, route_value = self._connect({'route': route})
                    database_name = str(
                        native.get('database') or
                        route_value.get('database') or 'admin'
                    )
                    collection_name = native.get('collection')
                    count = client[database_name][
                        collection_name
                    ].count_documents(
                        self._from_extended_json(selector),
                        limit=100001,
                    )
                    preview_observations['matched_document_count'] = count
                    preview_observations['count_bounded_at'] = 100001
                    if kind == 'collection' and count > 1:
                        warnings.append(
                            'Bulk operation currently matches '
                            f'{count} documents.'
                        )
                except Exception as exc:
                    warnings.append(
                        'Document match count could not be observed '
                        f'({type(exc).__name__}).'
                    )
                finally:
                    if client is not None:
                        self._forget_client(client)
        if kind == 'document' and operation == 'update':
            changes = draft.get('changes', {})
            if isinstance(changes, Mapping):
                preview_observations['document_diff'] = {
                    'set_or_replace': sorted(
                        key for key in changes if key != '$unset'
                    ),
                    'unset': sorted(
                        changes.get('$unset', {})
                        if isinstance(changes.get('$unset'), Mapping) else []
                    ),
                    'nested_paths_preserved': True,
                }
                if '_id' in changes or '$set' in changes and isinstance(
                    changes.get('$set'), Mapping
                ) and '_id' in changes['$set']:
                    warnings.append('MongoDB _id is immutable.')
        return {
            'command_preview': {
                'driver': 'pymongo', 'resource_kind': kind,
                'operation': operation, 'target': native,
                'arguments': preview_draft,
                'observations': preview_observations,
            },
            'provider_payload': provider_payload,
            'warnings': warnings,
            'receipt': {'provider_owned': True},
        }

    def apply_admin_operation(self, request):
        payload = _mapping(request.get('provider_payload'), 'provider plan')
        route = payload.pop('_provider_route', None)
        client, route_value = self._connect({'route': route})
        try:
            result = self._apply_admin(client, route_value, payload)
            return {
                **result,
                'driver_observation_only': True,
                'transaction_finality_interpreted_by_common_code': False,
            }
        except MongoDBClientError:
            raise
        except Exception as exc:
            raise MongoDBClientError(
                'MongoDB visual administration failed '
                f'({type(exc).__name__})'
            ) from None
        finally:
            self._forget_client(client)

    def _apply_admin(self, client, route, payload):
        kind = payload['resource_kind']
        operation = payload['operation_id']
        draft = self._from_extended_json(payload['draft'])
        native = self._from_extended_json(payload['native'])
        if kind in {'collection', 'view'} and operation == 'create':
            database_name = self._creation_database(
                {'target_resource': {'native': native}}, draft)
        else:
            database_name = str(
                native.get('database') or
                draft.get('options', {}).get('database') or
                route.get('database') or 'admin'
            )
        database = client[database_name]
        collection_name = native.get('collection')
        if operation == 'inspect':
            return self._inspect_admin(client, database, kind, native)
        if kind == 'database' and operation == 'drop':
            client.drop_database(database_name)
        elif kind in {'collection', 'view'}:
            self._apply_collection(
                database, kind, operation, draft, collection_name, native
            )
        elif kind == 'document':
            self._apply_document(
                database, operation, draft, collection_name
            )
        elif kind == 'index':
            self._apply_index(database, operation, draft, native)
        elif kind == 'validator':
            self._apply_validator(database, operation, draft, native)
        elif kind == 'aggregation-pipeline':
            return self._apply_aggregation_pipeline(
                database, draft, collection_name
            )
        elif kind == 'user':
            self._apply_user(database, operation, draft, native, route)
        elif kind == 'role':
            self._apply_role(database, operation, draft, native)
        elif kind in {
            'deployment', 'replica-set', 'shard', 'router', 'zone',
            'balancer', 'change-stream', 'profiling', 'current-operation',
        }:
            return self._apply_topology_operation(
                client, database, kind, operation, draft, native
            )
        elif kind in {'backup', 'restore', 'import', 'export', 'shell'}:
            return self._apply_tool(client, kind, draft, route)
        else:
            raise MongoDBClientError(
                'MongoDB visual administration operation is unavailable'
            )
        return {'acknowledged': True, 'operation': operation}

    def _apply_aggregation_pipeline(self, database, draft, collection_name):
        if not collection_name:
            raise MongoDBClientError(
                'MongoDB aggregation collection is unavailable'
            )
        pipeline = draft.get('pipeline')
        if not isinstance(pipeline, list) or any(
                not isinstance(stage, Mapping) for stage in pipeline):
            raise MongoDBClientError(
                'MongoDB aggregation pipeline is invalid'
            )
        if any(
            set(stage).intersection({'$out', '$merge'}) for stage in pipeline
        ):
            raise MongoDBClientError(
                'MongoDB read-only aggregation cannot mutate data'
            )
        options = _mapping(draft.get('options', {}), 'aggregation options')
        allowed = {
            'allowDiskUse', 'batchSize', 'bypassDocumentValidation',
            'collation', 'comment', 'hint', 'let', 'maxAwaitTimeMS',
            'maxTimeMS',
        }
        unknown = sorted(set(options).difference(allowed))
        if unknown:
            raise MongoDBClientError(
                'MongoDB aggregation options are unsupported: ' +
                ', '.join(unknown)
            )
        maximum = _bounded_int(
            draft.get('max_documents'), 100, 1, MAX_RESULT_DOCUMENTS,
            'maximum result documents',
        )
        cursor = database[collection_name].aggregate(pipeline, **options)
        try:
            values = list(islice(cursor, maximum + 1))
        finally:
            close = getattr(cursor, 'close', None)
            if callable(close):
                close()
        truncated = len(values) > maximum
        values = values[:maximum]
        return {
            'acknowledged': True, 'operation': 'execute',
            'observation': {
                'documents': [self._extended_json(item) for item in values],
                'document_count': len(values), 'truncated': truncated,
            },
            'driver_observation_only': True,
        }

    def _inspect_admin(self, client, database, kind, native):
        try:
            if kind == 'replica-set':
                value = {
                    'status': client.admin.command({'replSetGetStatus': 1}),
                    'configuration': client.admin.command({
                        'replSetGetConfig': 1,
                    }),
                }
            elif kind == 'current-operation':
                value = client.admin.command({
                    'currentOp': 1, '$all': True, 'idleConnections': False,
                })
            elif kind == 'server-log':
                value = client.admin.command({
                    'getLog': native.get('log', 'global')
                })
            elif kind == 'profiling':
                value = {
                    'status': database.command({'profile': -1}),
                    'recent': list(database['system.profile'].find({}).sort(
                        [('$natural', -1)]
                    ).limit(100)),
                }
            elif kind == 'statistics':
                value = database.command({'dbStats': 1, 'scale': 1})
            elif kind == 'balancer':
                value = client.admin.command({'balancerStatus': 1})
            else:
                value = native
            return {
                'acknowledged': True, 'operation': 'inspect',
                'observation': self._extended_json(value),
                'driver_observation_only': True,
            }
        except Exception as exc:
            raise MongoDBClientError(
                f'MongoDB inspection failed ({type(exc).__name__})'
            ) from None

    def _apply_topology_operation(
        self, client, database, kind, operation, draft, native
    ):
        changes = draft.get('changes', {})
        arguments = draft.get('arguments', draft.get('options', changes))
        arguments = _mapping(arguments or {}, 'operation arguments')
        action = draft.get('action') or arguments.pop('action', None)
        if operation == 'alter' and action is None:
            action = 'reconfigure' if kind == 'replica-set' else 'update'
        command = None
        if kind == 'replica-set':
            if action == 'reconfigure':
                config = arguments.get('config', changes.get('config'))
                if not isinstance(config, Mapping):
                    raise MongoDBClientError(
                        'replica-set reconfiguration requires config'
                    )
                command = {
                    'replSetReconfig': copy.deepcopy(config),
                    'force': bool(arguments.get('force', False)),
                }
            elif action == 'step_down':
                command = {
                    'replSetStepDown': _bounded_int(
                        arguments.get('seconds'), 60, 1, 86400,
                        'step-down seconds'
                    ),
                    'force': bool(arguments.get('force', False)),
                }
            elif action == 'freeze':
                command = {'replSetFreeze': _bounded_int(
                    arguments.get('seconds'), 60, 0, 86400,
                    'freeze seconds'
                )}
            elif action == 'sync_from':
                command = {'replSetSyncFrom': _identifier(
                    arguments.get('member'), 'replica-set member'
                )}
        elif kind in {'deployment', 'shard'}:
            if action == 'add_shard':
                command = {'addShard': _identifier(
                    arguments.get('connection'), 'shard connection'
                )}
                if arguments.get('name'):
                    command['name'] = _identifier(
                        arguments['name'], 'shard name'
                    )
            elif action == 'remove_shard':
                shard = arguments.get('shard') or native.get(
                    'shard', {}
                ).get('_id')
                command = {'removeShard': _identifier(shard, 'shard')}
            elif action == 'enable_sharding':
                command = {'enableSharding': _identifier(
                    arguments.get('database'), 'database'
                )}
            elif action in {
                'shard_collection', 'reshard_collection',
                'unshard_collection', 'move_collection',
            }:
                names = {
                    'shard_collection': 'shardCollection',
                    'reshard_collection': 'reshardCollection',
                    'unshard_collection': 'unshardCollection',
                    'move_collection': 'moveCollection',
                }
                command = {names[action]: _identifier(
                    arguments.get('namespace'), 'namespace'
                )}
                command.update(copy.deepcopy(arguments.get('options', {})))
            elif action == 'move_primary':
                command = {
                    'movePrimary': _identifier(
                        arguments.get('database'), 'database'
                    ),
                    'to': _identifier(arguments.get('to'), 'target shard'),
                }
        elif kind == 'router' and action == 'flush_configuration':
            command = {'flushRouterConfig': 1}
        elif kind == 'zone':
            zone = native.get('zone', {})
            if action in {'assign_shard', 'remove_shard'}:
                command = {
                    'addShardToZone' if action == 'assign_shard'
                    else 'removeShardFromZone': _identifier(
                        arguments.get('shard'), 'shard'
                    ),
                    'zone': _identifier(arguments.get('zone'), 'zone'),
                }
            else:
                namespace = arguments.get('namespace') or zone.get('ns')
                minimum = arguments.get('min', zone.get('min'))
                maximum = arguments.get('max', zone.get('max'))
                zone_name = arguments.get('zone', zone.get('tag'))
                if operation == 'drop':
                    zone_name = None
                if operation == 'alter':
                    old_namespace = _identifier(
                        zone.get('ns'), 'existing zone namespace'
                    )
                    old_minimum = _mapping(
                        zone.get('min'), 'existing zone minimum'
                    )
                    old_maximum = _mapping(
                        zone.get('max'), 'existing zone maximum'
                    )
                    old_name = _identifier(
                        zone.get('tag'), 'existing zone'
                    )
                    remove = {
                        'updateZoneKeyRange': old_namespace,
                        'min': old_minimum, 'max': old_maximum,
                        'zone': None,
                    }
                    replacement = {
                        'updateZoneKeyRange': _identifier(
                            namespace, 'zone namespace'
                        ),
                        'min': _mapping(minimum, 'zone minimum'),
                        'max': _mapping(maximum, 'zone maximum'),
                        'zone': _identifier(zone_name, 'zone'),
                    }
                    try:
                        removed = client.admin.command(remove)
                        replaced = client.admin.command(replacement)
                    except Exception as exc:
                        try:
                            client.admin.command({
                                'updateZoneKeyRange': old_namespace,
                                'min': old_minimum, 'max': old_maximum,
                                'zone': old_name,
                            })
                        except Exception:
                            pass
                        raise MongoDBClientError(
                            'MongoDB zone replacement failed and the '
                            'provider attempted to restore the original '
                            f'mapping ({type(exc).__name__})'
                        ) from None
                    return {
                        'acknowledged': bool(
                            removed.get('ok', 0) and replaced.get('ok', 0)
                        ),
                        'operation': operation,
                        'observation': self._extended_json({
                            'removed': removed, 'replacement': replaced,
                        }),
                        'driver_observation_only': True,
                        'transaction_finality_interpreted_by_common_code': (
                            False
                        ),
                    }
                command = {
                    'updateZoneKeyRange': _identifier(
                        namespace, 'zone namespace'
                    ),
                    'min': _mapping(minimum, 'zone minimum'),
                    'max': _mapping(maximum, 'zone maximum'),
                    'zone': zone_name,
                }
        elif kind == 'balancer':
            if action in {'start', 'stop', 'status'}:
                command = {
                    {'start': 'balancerStart', 'stop': 'balancerStop',
                     'status': 'balancerStatus'}[action]: 1
                }
            elif action == 'collection_status':
                command = {'balancerCollectionStatus': _identifier(
                    arguments.get('namespace'), 'namespace'
                )}
        elif kind == 'profiling' and action == 'set':
            command = {
                'profile': _bounded_int(
                    arguments.get('level'), 0, 0, 2, 'profiling level'
                )
            }
            if arguments.get('slow_ms') is not None:
                command['slowms'] = _bounded_int(
                    arguments['slow_ms'], 100, 0, 3600000,
                    'profiling slow milliseconds'
                )
            if arguments.get('sample_rate') is not None:
                rate = arguments['sample_rate']
                if (
                    isinstance(rate, bool) or
                    not isinstance(rate, (int, float)) or
                    not 0 <= rate <= 1
                ):
                    raise MongoDBClientError(
                        'profiling sample rate must be between 0 and 1'
                    )
                command['sampleRate'] = rate
        elif kind == 'current-operation' and action == 'kill':
            command = {'killOp': 1, 'op': arguments.get('operation_id')}
        elif kind == 'change-stream' and action == 'open':
            return {
                'acknowledged': True, 'operation': operation,
                'query_template': {
                    'operation': 'watch',
                    'database': native.get('database'),
                    'collection': native.get('collection'),
                    **copy.deepcopy(arguments),
                },
                'driver_observation_only': True,
            }
        if command is None:
            raise MongoDBClientError(
                'MongoDB topology or operational action is unavailable'
            )
        try:
            result = database.command(command) if kind == 'profiling' else (
                client.admin.command(command)
            )
        except Exception as exc:
            raise MongoDBClientError(
                'MongoDB topology or operational action failed '
                f'({type(exc).__name__}: {str(exc)[:500]})'
            ) from None
        return {
            'acknowledged': bool(result.get('ok', 0)),
            'operation': operation,
            'observation': self._extended_json(result),
            'driver_observation_only': True,
            'transaction_finality_interpreted_by_common_code': False,
        }

    @staticmethod
    def _workspace_path(workspace, value, label):
        root = Path(_identifier(workspace, 'tool workspace')).resolve(
            strict=False
        )
        if not root.is_absolute() or root in {Path('/'), Path.home()}:
            raise MongoDBClientError('tool workspace grant is unsafe')
        value = _identifier(value, label)
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError:
            raise MongoDBClientError(
                f'{label} escapes the tool workspace'
            ) from None
        if candidate == root:
            raise MongoDBClientError(f'{label} must identify a child path')
        return root, candidate

    @staticmethod
    def _tool_arguments(route):
        arguments = [
            f"--host={route['host']}",
            f"--port={route.get('port', 27017)}",
        ]
        if route.get('username'):
            arguments.append(f"--username={route['username']}")
        auth_source = route.get('auth_source') or route.get('database')
        if auth_source:
            arguments.append(f'--authenticationDatabase={auth_source}')
        if route.get('tls'):
            arguments.append('--ssl')
        if route.get('tls_ca_file'):
            arguments.append(f"--sslCAFile={route['tls_ca_file']}")
        if route.get('tls_certificate_key_file'):
            arguments.append(
                '--sslPEMKeyFile=' + route['tls_certificate_key_file']
            )
        return arguments

    def _tool_grant(self, executable, route):
        return ProviderToolGrant(
            executable, _identifier(
                route.get('tool_workspace'), 'tool workspace'
            ), _identifier(route.get('host'), 'endpoint host'),
            _bounded_int(route.get('port'), 27017, 1, 65535, 'endpoint port'),
        )

    def _tool_secret(self, route, callback):
        reference = route.get('credential_reference_id')
        if reference is None:
            return callback(None)
        return self._use_admin_secret(
            route, {'credential_reference_id': reference}, callback,
            purpose='provider_tool',
        )

    def _apply_tool(self, client, kind, draft, route):
        action = _identifier(draft.get('action'), 'tool action')
        arguments = _mapping(
            draft.get('arguments', {}), 'tool arguments'
        )
        workspace, path = self._workspace_path(
            route.get('tool_workspace'),
            arguments.get('path', '.cdeadmin-shell-observation'),
            'tool path'
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        database_name = _identifier(
            arguments.get('database', route.get('database') or 'admin'),
            'database'
        )
        collection_name = arguments.get('collection')
        if collection_name is not None:
            collection_name = _identifier(collection_name, 'collection')

        # Canonical Extended JSON and JSON Lines are implemented through the
        # driver so BSON identity is never degraded by a generic JSON parser.
        if kind == 'export' and action in {'extended_json', 'json_lines'}:
            limit = _bounded_int(
                arguments.get('max_documents'), 10000, 1,
                MAX_STREAM_DOCUMENTS, 'export document limit'
            )
            selector = self._from_extended_json(
                _mapping(arguments.get('filter', {}), 'export filter')
            )
            cursor = client[database_name][collection_name].find(
                selector
            ).limit(limit)
            count = 0
            digest = hashlib.sha256()
            with path.open('wb') as stream:
                if action == 'extended_json':
                    stream.write(b'[')
                    digest.update(b'[')
                for document in cursor:
                    value = self._json_util.dumps(
                        document,
                        json_options=self._json_util.CANONICAL_JSON_OPTIONS,
                        ensure_ascii=False,
                    ).encode('utf-8')
                    prefix = b'' if count == 0 or action == 'json_lines' \
                        else b','
                    suffix = b'\n' if action == 'json_lines' else b''
                    stream.write(prefix + value + suffix)
                    digest.update(prefix + value + suffix)
                    count += 1
                if action == 'extended_json':
                    stream.write(b']')
                    digest.update(b']')
            return {
                'acknowledged': True, 'operation': action,
                'document_count': count, 'path': str(path),
                'sha256': digest.hexdigest(),
                'encoding': 'mongodb-canonical-extended-json',
                'driver_observation_only': True,
                'transaction_finality_interpreted_by_common_code': False,
            }
        if kind == 'import' and action in {'extended_json', 'json_lines'}:
            if not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
                raise MongoDBClientError(
                    'import file is unavailable or exceeds 128 MiB'
                )
            raw = path.read_text(encoding='utf-8')
            if action == 'extended_json':
                decoded = self._json_util.loads(raw)
                documents = decoded if isinstance(decoded, list) else [decoded]
            else:
                documents = [
                    self._json_util.loads(line) for line in raw.splitlines()
                    if line.strip()
                ]
            if len(documents) > MAX_STREAM_DOCUMENTS or any(
                not isinstance(item, Mapping) for item in documents
            ):
                raise MongoDBClientError('import document set is invalid')
            result = client[database_name][collection_name].insert_many(
                documents, ordered=bool(arguments.get('ordered', True))
            ) if documents else None
            return {
                'acknowledged': bool(
                    result is None or result.acknowledged
                ),
                'operation': action, 'document_count': len(documents),
                'driver_observation_only': True,
                'transaction_finality_interpreted_by_common_code': False,
            }

        executable = {
            'backup': 'mongodump', 'restore': 'mongorestore',
            'import': 'mongoimport', 'export': 'mongoexport',
            'shell': 'mongosh',
        }[kind]
        grant = self._tool_grant(executable, route)
        cli = self._tool_arguments(route)
        if kind == 'backup' and action == 'archive':
            cli.extend((f'--archive={path}', f'--db={database_name}'))
            if collection_name:
                cli.append(f'--collection={collection_name}')
            if arguments.get('gzip'):
                cli.append('--gzip')
            if arguments.get('oplog'):
                cli.append('--oplog')
        elif kind == 'restore' and action == 'archive':
            if not path.is_file():
                raise MongoDBClientError('restore archive is unavailable')
            cli.append(f'--archive={path}')
            if arguments.get('gzip'):
                cli.append('--gzip')
            if arguments.get('drop'):
                cli.append('--drop')
            for source, flag in (
                ('namespace_include', '--nsInclude'),
                ('namespace_from', '--nsFrom'),
                ('namespace_to', '--nsTo'),
            ):
                if arguments.get(source):
                    cli.append(f'{flag}={arguments[source]}')
        elif kind == 'export' and action in {'json', 'csv'}:
            cli.extend((
                f'--db={database_name}',
                f'--collection={_identifier(collection_name, "collection")}',
                f'--out={path}', f'--type={action}',
            ))
            if arguments.get('query'):
                cli.append('--query=' + json.dumps(arguments['query']))
            if action == 'json' and arguments.get('json_array'):
                cli.append('--jsonArray')
            if action == 'csv':
                cli.append('--fields=' + _identifier(
                    arguments.get('fields'), 'CSV fields'
                ))
        elif kind == 'import' and action in {'json', 'csv'}:
            if not path.is_file():
                raise MongoDBClientError('import file is unavailable')
            cli.extend((
                f'--db={database_name}',
                f'--collection={_identifier(collection_name, "collection")}',
                f'--file={path}', f'--type={action}',
            ))
            if action == 'json' and arguments.get('json_array'):
                cli.append('--jsonArray')
            if action == 'csv' and arguments.get('headerline'):
                cli.append('--headerline')
            if arguments.get('mode'):
                cli.append('--mode=' + _identifier(
                    arguments['mode'], 'import mode'
                ))
        elif kind != 'shell' or action != 'script':
            raise MongoDBClientError('MongoDB tool action is unavailable')

        def invoke(password):
            if kind == 'shell':
                script = _identifier(arguments.get('script'), 'shell script')
                username = quote(str(route.get('username') or ''), safe='')
                encoded = quote(str(password or ''), safe='')
                auth_source = quote(str(
                    route.get('auth_source') or database_name
                ), safe='')
                uri = (
                    f'mongodb://{username}:{encoded}@{route["host"]}:'
                    f'{route.get("port", 27017)}/'
                    f'{quote(database_name, safe="")}'
                    f'?authSource={auth_source}'
                )
                bootstrap = (
                    f'const __cde = new Mongo({json.dumps(uri)});\n'
                    f'const db = __cde.getDB({json.dumps(database_name)});\n'
                    f'const __result = (() => {{\n{script}\n}})();\n'
                    'if (__result !== undefined) print(EJSON.stringify('
                    '__result, {relaxed: false}));\n'
                ).encode('utf-8')
                return self._tool_runner.run(
                    grant, ['--quiet', '--norc', '--nodb'],
                    secret_config=bootstrap,
                    secret_argument='--file={path}', secret_suffix='.js',
                    redact_values=(str(password or ''), encoded),
                )
            config = (
                'password: ' + json.dumps(str(password or '')) + '\n'
            ).encode('utf-8') if route.get('username') else None
            return self._tool_runner.run(
                grant, cli, secret_config=config,
                redact_values=(str(password or ''),)
            )

        try:
            observation = self._tool_secret(route, invoke)
        except ProviderToolError as exc:
            raise MongoDBClientError(str(exc)) from None
        if observation['return_code'] != 0:
            raise MongoDBClientError(
                f'MongoDB {executable} exited with a failure: '
                f"{observation['stderr'][-500:]}"
            )
        if kind == 'backup' and not path.is_file():
            raise MongoDBClientError('MongoDB backup archive was not created')
        observation['operation'] = action
        observation['acknowledged'] = True
        if path.is_file() and kind in {'backup', 'export'}:
            observation['artifact'] = {
                'path': str(path), 'bytes': path.stat().st_size,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        return observation

    @staticmethod
    def _apply_collection(database, kind, operation, draft, name, native):
        if operation == 'create':
            name = _identifier(draft.get('name'), f'{kind} name')
            options = (MongoDBClient._view_creation_options(draft)
                       if kind == 'view' else
                       MongoDBClient._collection_options(draft))
            options.pop('database', None)
            if kind == 'view':
                view_on = _identifier(options.pop('view_on', None), 'view_on')
                pipeline = options.pop('pipeline', [])
                database.create_collection(
                    name, viewOn=view_on, pipeline=pipeline, **options
                )
            else:
                database.create_collection(name, **options)
        elif operation == 'alter':
            changes = _mapping(draft.get('changes'), 'collection changes')
            if kind == 'view':
                current = native.get('options', {})
                changes.setdefault('viewOn', current.get('viewOn'))
                changes.setdefault('pipeline', current.get('pipeline', []))
            database.command({'collMod': name, **changes})
        elif operation == 'rename':
            database[name].rename(
                _identifier(draft.get('new_name'), 'new collection name')
            )
        elif operation == 'drop':
            database.drop_collection(name)
        elif operation == 'insert':
            database[name].insert_one(copy.deepcopy(draft['values']))
        elif operation == 'update':
            database[name].update_many(
                copy.deepcopy(draft['selector']),
                copy.deepcopy(draft['changes']),
            )
        elif operation == 'delete':
            database[name].delete_many(copy.deepcopy(draft['selector']))
        else:
            raise MongoDBClientError('collection operation is unavailable')

    @staticmethod
    def _apply_document(database, operation, draft, name):
        if operation == 'insert':
            database[name].insert_one(copy.deepcopy(draft['values']))
        elif operation == 'update':
            selector = copy.deepcopy(draft['selector'])
            changes = copy.deepcopy(draft['changes'])
            if any(str(key).startswith('$') for key in changes):
                database[name].update_one(selector, changes)
            else:
                if '_id' in selector and '_id' not in changes:
                    changes['_id'] = selector['_id']
                database[name].replace_one(selector, changes)
        elif operation == 'delete':
            database[name].delete_one(copy.deepcopy(draft['selector']))
        else:
            raise MongoDBClientError('document operation is unavailable')

    @staticmethod
    def _index_changes(draft):
        raw = draft.get('changes', {})
        allowed = {'hidden', 'expireAfterSeconds', 'prepareUnique', 'unique',
                   'forceNonUnique'}
        if not isinstance(raw, Mapping) or set(raw) - allowed:
            raise MongoDBClientError(
                'Index changes contain unsupported fields or an identity '
                'override.')
        changes = copy.deepcopy(dict(raw))

        def add(key, value):
            if key in changes:
                raise MongoDBClientError(
                    'Specify each index change once, visually or as native '
                    'changes, not both.')
            changes[key] = value

        visibility = draft.get('visibility', 'unchanged')
        if visibility not in ('unchanged', 'hidden', 'visible'):
            raise MongoDBClientError('Index visibility choice is invalid.')
        if visibility != 'unchanged':
            add('hidden', visibility == 'hidden')
        change_ttl = draft.get('change_ttl', False)
        if not isinstance(change_ttl, bool):
            raise MongoDBClientError('Change TTL must be true or false.')
        if change_ttl:
            add('expireAfterSeconds', draft.get('ttl_seconds'))
        choices = {'prepare': ('prepareUnique', True),
                   'cancel_prepare': ('prepareUnique', False),
                   'unique': ('unique', True),
                   'non_unique': ('forceNonUnique', True)}
        uniqueness = draft.get('uniqueness', 'unchanged')
        if uniqueness != 'unchanged':
            if not isinstance(uniqueness, str) or uniqueness not in choices:
                raise MongoDBClientError('Uniqueness choice is invalid.')
            add(*choices[uniqueness])
        if not changes:
            raise MongoDBClientError('Choose at least one index change.')
        for key, value in changes.items():
            if key == 'expireAfterSeconds':
                if type(value) is not int or not 0 <= value <= 2147483647:
                    raise MongoDBClientError(
                        'TTL must be an integer from 0 to 2147483647 seconds.')
            elif not isinstance(value, bool):
                raise MongoDBClientError('Index flags must be true or false.')
            elif key in {'unique', 'forceNonUnique'} and not value:
                raise MongoDBClientError(
                    'Unique conversion flags only accept true.')
        if {'unique', 'forceNonUnique'} <= set(changes):
            raise MongoDBClientError('Conflicting uniqueness conversions.')
        if 'prepareUnique' in changes and len(changes) != 1:
            raise MongoDBClientError(
                'Unique preparation must be performed separately.')
        return changes

    @staticmethod
    def _index_options(draft):
        options = draft.get('options', {})
        if not isinstance(options, Mapping):
            raise MongoDBClientError('Index options must be an object.')
        options = copy.deepcopy(dict(options))
        for field in ('unique', 'sparse', 'hidden'):
            choice = draft.get(field + '_mode', 'native')
            if choice not in ('native', 'enabled', 'disabled'):
                raise MongoDBClientError('Index option choice is invalid.')
            if choice != 'native':
                if field in options:
                    raise MongoDBClientError(
                        'Specify each index option once, visually or in '
                        'advanced options, not both.')
                options[field] = choice == 'enabled'
        enable_ttl = draft.get('enable_ttl', False)
        if not isinstance(enable_ttl, bool):
            raise MongoDBClientError('Enable TTL must be true or false.')
        if enable_ttl:
            if 'expireAfterSeconds' in options:
                raise MongoDBClientError(
                    'Specify TTL visually or in advanced options, not both.')
            ttl = draft.get('ttl_seconds')
            if type(ttl) is not int or not 0 <= ttl <= 2147483647:
                raise MongoDBClientError(
                    'TTL must be an integer from 0 to 2147483647 seconds.')
            options['expireAfterSeconds'] = ttl
        records = draft.get('keys', [])
        if not isinstance(records, list):
            raise MongoDBClientError('Ordered index keys must be a list.')
        if records:
            if options.get('keys'):
                raise MongoDBClientError(
                    'Specify visual keys or legacy options.keys, not both.')
            kinds = {'ascending': 1, 'descending': -1, 'text': 'text',
                     'hashed': 'hashed', '2d': '2d', '2dsphere': '2dsphere'}
            keys = []
            seen = set()
            for record in records:
                if not isinstance(record, Mapping) or set(record) != {
                        'field', 'kind'}:
                    raise MongoDBClientError(
                        'Each index key requires only field and kind.')
                name, kind = record['field'], record['kind']
                if not isinstance(name, str) or not name or '\x00' in name:
                    raise MongoDBClientError('Index field path is invalid.')
                if not isinstance(kind, str) or kind not in kinds:
                    raise MongoDBClientError('Index key type is unsupported.')
                if name in seen:
                    raise MongoDBClientError('Duplicate index field path.')
                seen.add(name)
                keys.append((name, kinds[kind]))
            options['keys'] = keys
        if not isinstance(options.get('keys'), list) or not options['keys']:
            raise MongoDBClientError('At least one index key is required.')
        MongoDBClient._text_index_options(draft, options)
        MongoDBClient._collation_options(draft, options)
        MongoDBClient._wildcard_index_options(draft, options)
        MongoDBClient._geo_index_options(draft, options)
        if 'name' in options and options['name'] != draft.get('name'):
            raise MongoDBClientError(
                'Native options cannot override the index name.')
        options.setdefault('name', draft.get('name'))
        return options

    @staticmethod
    def _geo_index_options(draft, options):
        mode = draft.get('geo_mode', 'native')
        if mode not in ('native', '2d', '2dsphere'):
            raise MongoDBClientError('Geospatial options choice is invalid.')
        if mode == 'native':
            return
        if not any(isinstance(key, (list, tuple)) and len(key) == 2 and
                   key[1] == mode for key in options['keys']):
            raise MongoDBClientError(
                'Geospatial settings require a matching native index key.')

        def add(key, value):
            if key in options:
                raise MongoDBClientError(
                    'Specify each geospatial option visually or in advanced '
                    'options, not both.')
            options[key] = value

        if mode == '2dsphere':
            version = draft.get('geo_version', 'native')
            if version not in ('native', '1', '2', '3'):
                raise MongoDBClientError('2dsphere version is invalid.')
            if version != 'native':
                add('2dsphereIndexVersion', int(version))
            return
        for key in ('bits', 'min', 'max'):
            value = draft.get('geo_' + key)
            if value is None or value == '':
                continue
            if key == 'bits':
                if type(value) is not int or not 1 <= value <= 32:
                    raise MongoDBClientError(
                        '2D bits must be an integer 1–32.')
            else:
                try:
                    finite = (type(value) in (int, float) and
                              math.isfinite(value))
                except OverflowError:
                    finite = False
                if not finite:
                    raise MongoDBClientError(
                        '2D bounds must be finite numbers.')
            add(key, value)
        lower, upper = options.get('min', -180), options.get('max', 180)
        if type(lower) in (int, float) and type(upper) in (int, float) and (
                lower >= upper):
            raise MongoDBClientError('2D minimum must be less than maximum.')

    @staticmethod
    def _wildcard_index_options(draft, options):
        enabled = draft.get('configure_wildcard', False)
        if not isinstance(enabled, bool):
            raise MongoDBClientError(
                'Configure wildcard must be true or false.')
        if not enabled:
            return
        if 'wildcardProjection' in options:
            raise MongoDBClientError(
                'Specify wildcard projection visually or in advanced options, '
                'not both.')
        if not any(isinstance(key, (list, tuple)) and len(key) == 2 and
                   key[0] == '$**' and key[1] in (1, -1)
                   for key in options['keys']):
            raise MongoDBClientError(
                'Wildcard projection requires a root $** ascending or '
                'descending index key.')
        records = draft.get('wildcard_fields')
        if not isinstance(records, list) or not records:
            raise MongoDBClientError('Wildcard projection cannot be empty.')
        projection = {}
        for record in records:
            if not isinstance(record, Mapping) or set(record) != {
                    'field', 'action'}:
                raise MongoDBClientError(
                    'Each projection field requires field and action.')
            field, action = record['field'], record['action']
            if not isinstance(field, str) or not field or '\x00' in field:
                raise MongoDBClientError('Projection field path is invalid.')
            if field in projection:
                raise MongoDBClientError('Duplicate projection field path.')
            if action not in ('include', 'exclude'):
                raise MongoDBClientError('Projection action is invalid.')
            projection[field] = int(action == 'include')
        modes = {value for key, value in projection.items() if key != '_id'}
        if len(modes) > 1:
            raise MongoDBClientError(
                'Cannot mix inclusion and exclusion except for _id.')
        options['wildcardProjection'] = projection

    @classmethod
    def _collation_fields(cls, scope='index'):
        f = cls._field
        visible = {'field_id': 'collation_mode', 'equals': 'locale'}
        default_label = ('Collection default / advanced options'
                         if scope == 'index' else
                         'Native default / advanced options')
        fields = [f('collation_mode', scope.title() + ' collation', 'select',
                    default='native', options=[
                        {'value': 'native',
                         'label': default_label},
                        {'value': 'simple', 'label': 'Binary (simple)'},
                        {'value': 'locale', 'label': 'Locale-specific'},
                    ]),
                  f('collation_locale', 'Locale', required=True,
                    visible_when=visible)]
        choices = {
            'strength': ('1', '2', '3', '4', '5'),
            'caseFirst': ('upper', 'lower', 'off'),
            'alternate': ('non-ignorable', 'shifted'),
            'maxVariable': ('punct', 'space'),
            **{key: ('enabled', 'disabled') for key in (
                'caseLevel', 'numericOrdering', 'normalization', 'backwards')},
        }
        labels = {'strength': 'Comparison strength',
                  'caseFirst': 'Case ordering',
                  'alternate': 'Alternate handling',
                  'maxVariable': 'Maximum variable character class',
                  'caseLevel': 'Separate case level',
                  'numericOrdering': 'Numeric ordering',
                  'normalization': 'Unicode normalization',
                  'backwards': 'Reverse accent ordering'}
        for key, values in choices.items():
            fields.append(f('collation_' + key, labels[key], 'select',
                            default='native', visible_when=visible,
                            options=[{'value': 'native',
                                      'label': 'Native locale default'}] + [
                                {'value': value, 'label': value}
                                for value in values]))
        fields.append(f('collation_version',
                        'Required collation version (blank uses server)',
                        visible_when=visible))
        return fields

    @classmethod
    def _view_creation_options(cls, draft):
        options = cls._collection_options(draft)
        source = draft.get('view_source')
        if source is not None and source != '':
            if 'view_on' in options or 'viewOn' in options:
                raise MongoDBClientError(
                    'Specify the view source visually or in options, '
                    'not both.')
            options['view_on'] = source
        if 'viewOn' in options:
            if 'view_on' in options:
                raise MongoDBClientError('Specify the view source only once.')
            options['view_on'] = options.pop('viewOn')
        options['view_on'] = _identifier(
            options.get('view_on'), 'view source')
        configure = draft.get('configure_pipeline', False)
        if not isinstance(configure, bool):
            raise MongoDBClientError('Define pipeline must be true or false.')
        if configure:
            if 'pipeline' in options:
                raise MongoDBClientError(
                    'Specify the pipeline visually or in options, not both.')
            options['pipeline'] = copy.deepcopy(draft.get('view_pipeline', []))
        pipeline = options.setdefault('pipeline', [])
        if not isinstance(pipeline, list) or any(
                not isinstance(stage, Mapping) or len(stage) != 1
                for stage in pipeline):
            raise MongoDBClientError(
                'View pipeline must be an array of single-stage objects.')
        return options

    @classmethod
    def _creation_database(cls, request, draft):
        target = request.get('target_resource')
        native = cls._native_target(target) if target else {}
        options = draft.get('options', {})
        if not isinstance(options, Mapping):
            raise MongoDBClientError('Creation options must be an object.')
        supplied = [native.get('database'), draft.get('database'),
                    options.get('database')]
        values = []
        for value in supplied:
            if value is None or value == '':
                continue
            values.append(_identifier(value, 'database'))
        if not values:
            raise MongoDBClientError(
                'Select or enter a database for creation.')
        if len(set(values)) != 1:
            raise MongoDBClientError(
                'Database conflicts with the selected target or creation '
                'options. Select the intended database before creating.')
        return values[0]

    @staticmethod
    def _collection_options(draft):
        raw = draft.get('options', {})
        if not isinstance(raw, Mapping):
            raise MongoDBClientError(
                'Collection/view options must be an object.')
        options = copy.deepcopy(dict(raw))
        MongoDBClient._collation_options(draft, options)
        return options

    @staticmethod
    def _collation_options(draft, options):
        mode = draft.get('collation_mode', 'native')
        if mode not in ('native', 'simple', 'locale'):
            raise MongoDBClientError('Collation choice is invalid.')
        if mode == 'native':
            return
        if 'collation' in options:
            raise MongoDBClientError(
                'Specify collation visually or in advanced options, not both.')
        if mode == 'simple':
            options['collation'] = {'locale': 'simple'}
            return
        locale = draft.get('collation_locale')
        if not isinstance(locale, str) or not locale or '\x00' in locale:
            raise MongoDBClientError('A valid collation locale is required.')
        collation = {'locale': locale}
        choices = {'strength': ('1', '2', '3', '4', '5'),
                   'caseFirst': ('upper', 'lower', 'off'),
                   'alternate': ('non-ignorable', 'shifted'),
                   'maxVariable': ('punct', 'space')}
        booleans = ('caseLevel', 'numericOrdering',
                    'normalization', 'backwards')
        choices.update({key: ('enabled', 'disabled') for key in booleans})
        for key, allowed in choices.items():
            value = draft.get('collation_' + key, 'native')
            if value == 'native':
                continue
            if value not in allowed:
                raise MongoDBClientError('Collation option is invalid: ' + key)
            collation[key] = (value == 'enabled' if key in booleans else
                              int(value) if key == 'strength' else value)
        version = draft.get('collation_version', '')
        if not isinstance(version, str) or '\x00' in version:
            raise MongoDBClientError('Collation version must be text.')
        if version:
            collation['version'] = version
        options['collation'] = collation

    @staticmethod
    def _text_index_options(draft, options):
        enabled = draft.get('configure_text', False)
        if not isinstance(enabled, bool):
            raise MongoDBClientError('Configure text must be true or false.')
        if not enabled:
            return
        if not any(isinstance(key, (list, tuple)) and len(key) == 2 and
                   key[1] == 'text' for key in options['keys']):
            raise MongoDBClientError('Text options require a text index key.')

        def add(key, value):
            if key in options:
                raise MongoDBClientError(
                    'Specify each text option once, visually or in advanced '
                    'options, not both.')
            options[key] = value

        records = draft.get('text_weights', [])
        if not isinstance(records, list):
            raise MongoDBClientError('Text weights must be a list.')
        weights = {}
        for record in records:
            if not isinstance(record, Mapping) or set(record) != {
                    'field', 'weight'}:
                raise MongoDBClientError(
                    'Each weight requires field and weight.')
            field, weight = record['field'], record['weight']
            if not isinstance(field, str) or not field or '\x00' in field or (
                    field != '$**' and any(
                        not part or part.startswith('$')
                        for part in field.split('.'))):
                raise MongoDBClientError('Weighted document field is invalid.')
            if field in weights:
                raise MongoDBClientError('Duplicate weighted document field.')
            if type(weight) is not int or not 1 <= weight <= 99999:
                raise MongoDBClientError(
                    'Weight must be an integer 1 to 99999.')
            weights[field] = weight
        if weights:
            add('weights', weights)
        for field, native in (
                ('text_language', 'default_language'),
                ('text_language_override', 'language_override')):
            value = draft.get(field, '')
            if not isinstance(value, str) or '\x00' in value:
                raise MongoDBClientError(
                    'Text language settings must be text.')
            if value:
                if native == 'language_override' and (
                        value.startswith('$') or '.' in value):
                    raise MongoDBClientError('Language field is invalid.')
                add(native, value)
        version = draft.get('text_version', 'native')
        if version not in ('native', '2', '3'):
            raise MongoDBClientError('Text index version choice is invalid.')
        if version != 'native':
            add('textIndexVersion', int(version))

    @staticmethod
    def _apply_index(database, operation, draft, native):
        collection = database[native.get('collection')]
        if operation == 'create':
            options = MongoDBClient._index_options(draft)
            keys = options.pop('keys', None)
            if not isinstance(keys, list):
                raise MongoDBClientError('index options require keys array')
            options.setdefault('name', draft.get('name'))
            collection.create_index(keys, **options)
        elif operation == 'alter':
            database.command({
                'collMod': native.get('collection'),
                'index': {
                    'name': native.get('index_name'),
                    **MongoDBClient._index_changes(draft),
                },
            })
        elif operation == 'drop':
            collection.drop_index(native.get('index_name'))
        else:
            raise MongoDBClientError('index operation is unavailable')

    @staticmethod
    def _validator_changes(operation, draft):
        allowed = {'validator', 'validationLevel', 'validationAction'}
        raw = draft.get('changes' if operation == 'alter' else 'options', {})
        if not isinstance(raw, Mapping):
            raise MongoDBClientError('Validation changes must be an object.')
        if raw and ('validator' in raw or set(raw) <= allowed):
            if set(raw) - allowed:
                raise MongoDBClientError(
                    'Unsupported validation change field.')
            changes = copy.deepcopy(dict(raw))
        else:
            changes = {'validator': copy.deepcopy(dict(raw))} if raw else {}

        def add(key, value):
            if key in changes:
                raise MongoDBClientError(
                    'Specify each validation change once, visually or '
                    'natively.')
            changes[key] = copy.deepcopy(value)

        replace = draft.get('replace_rule', False)
        if not isinstance(replace, bool):
            raise MongoDBClientError('Replace rule must be true or false.')
        if replace:
            add('validator', draft.get('validator', {}))
        for field, native in (('validation_level', 'validationLevel'),
                              ('validation_action', 'validationAction')):
            if draft.get(field, 'unchanged') != 'unchanged':
                add(native, draft[field])
        if not changes:
            raise MongoDBClientError('Choose at least one validation change.')
        if 'validator' in changes and not isinstance(
                changes['validator'], Mapping):
            raise MongoDBClientError('Validation rule must be an object.')
        if 'validationLevel' in changes and changes['validationLevel'] not in (
                'off', 'strict', 'moderate'):
            raise MongoDBClientError('Validation level is unsupported.')
        if 'validationAction' in changes and (
                changes['validationAction'] not in
                ('error', 'warn', 'errorAndLog')):
            raise MongoDBClientError('Validation action is unsupported.')
        return changes

    @staticmethod
    def _apply_validator(database, operation, draft, native):
        if operation == 'drop':
            changes = {'validator': {}}
        elif operation in {'create', 'alter'}:
            changes = MongoDBClient._validator_changes(operation, draft)
        else:
            raise MongoDBClientError('Validator operation is unavailable.')
        database.command({'collMod': native.get('collection'), **changes})

    def _use_admin_secret(
        self, route, container, callback, purpose='administer'
    ):
        reference_id = _identifier(
            container.get('credential_reference_id'),
            'credential reference',
        )
        principal = _identifier(
            route.get('principal_reference'), 'principal reference'
        )
        if not callable(self._secret_acquirer):
            raise MongoDBClientError(
                'MongoDB credential binding is unavailable'
            )
        lease = self._secret_acquirer(
            reference_id, principal, purpose, 'database_password'
        )
        with lease:
            return lease.use(
                lambda view: callback(bytes(view).decode('utf-8'))
            )

    def _apply_user(self, database, operation, draft, native, route):
        user = native.get('user') or draft.get('name')
        if operation in {'create', 'alter'}:
            container = dict(
                draft.get('options' if operation == 'create' else 'changes')
            )
            container.pop('database', None)
            reference_id = container.pop('credential_reference_id', None)
            roles = container.pop('roles', [])
            command = 'createUser' if operation == 'create' else 'updateUser'
            value = {command: user, 'roles': roles, **container}
            if reference_id is None and operation == 'alter':
                database.command(value)
            else:
                self._use_admin_secret(
                    route, {'credential_reference_id': reference_id},
                    lambda password: database.command({
                        **value, 'pwd': password,
                    }),
                )
        elif operation == 'grant':
            database.command({
                'grantRolesToUser': user,
                'roles': copy.deepcopy(draft['privileges']),
            })
        elif operation == 'revoke':
            database.command({
                'revokeRolesFromUser': user,
                'roles': copy.deepcopy(draft['privileges']),
            })
        elif operation == 'drop':
            database.command({'dropUser': user})
        else:
            raise MongoDBClientError('user operation is unavailable')

    @staticmethod
    def _apply_role(database, operation, draft, native):
        role = native.get('role') or draft.get('name')
        if operation in {'create', 'alter'}:
            container = dict(
                draft.get('options' if operation == 'create' else 'changes')
            )
            container.pop('database', None)
            command = 'createRole' if operation == 'create' else 'updateRole'
            database.command({
                command: role,
                'privileges': container.pop('privileges', []),
                'roles': container.pop('roles', []),
                **container,
            })
        elif operation == 'grant':
            database.command({
                'grantPrivilegesToRole': role,
                'privileges': copy.deepcopy(draft['privileges']),
            })
        elif operation == 'revoke':
            database.command({
                'revokePrivilegesFromRole': role,
                'privileges': copy.deepcopy(draft['privileges']),
            })
        elif operation == 'drop':
            database.command({'dropRole': role})
        else:
            raise MongoDBClientError('role operation is unavailable')

    def read_admin_rows(self, request):
        target = self._native_target(request.get('target_resource'))
        database_name = _identifier(target.get('database'), 'database')
        collection_name = _identifier(
            target.get('collection'), 'collection'
        )
        limit = _bounded_int(
            request.get('limit'), 200, 1, MAX_BATCH_DOCUMENTS, 'row limit'
        )
        continuation = request.get('continuation')
        if continuation is not None:
            cursor_id = _identifier(continuation, 'document continuation')
            state = self._admin_cursors.get(cursor_id)
            if state is None or (
                state.database != database_name or
                state.collection != collection_name
            ):
                raise MongoDBClientError(
                    'MongoDB document continuation is unavailable'
                )
            return self._admin_cursor_page(state, target)
        if len(self._admin_cursors) >= MAX_ACTIVE_CURSORS:
            raise MongoDBClientError(
                'MongoDB active document cursor limit is reached'
            )
        filter_value = self._from_extended_json(
            _mapping(request.get('filter', {}), 'document filter')
        )
        projection = request.get('projection')
        if projection is not None:
            projection = self._from_extended_json(
                _mapping(projection, 'document projection')
            )
        sort = request.get('sort')
        if sort is not None and not isinstance(sort, list):
            raise MongoDBClientError('document sort must be an array')
        client, _route = self._connect(request)
        try:
            cursor = client[database_name][collection_name].find(
                filter_value, projection
            )
            if sort is not None:
                cursor = cursor.sort(sort)
            set_batch_size = getattr(cursor, 'batch_size', None)
            if callable(set_batch_size):
                cursor = set_batch_size(limit)
            cursor_id = str(uuid.uuid4())
            state = _MongoAdminCursor(
                cursor_id, cursor, database_name, collection_name, limit,
                client,
            )
            self._admin_cursors[cursor_id] = state
            if client in self._clients:
                self._clients.remove(client)
            return self._admin_cursor_page(state, target)
        except Exception:
            self._forget_client(client)
            raise

    def _admin_cursor_page(self, state, target):
        documents = list(islice(state.cursor, state.batch_size))
        complete = len(documents) < state.batch_size
        if complete:
            self._admin_cursors.pop(state.cursor_id, None)
            state.close()
        encoded = [self._extended_json(item) for item in documents]
        return {
            'schema': 'cdeadmin.visual-admin.document-page.v2',
            'resource_kind': 'collection',
            'encoding': 'mongodb-canonical-extended-json',
            'documents': encoded,
            'complete': complete,
            'continuation': None if complete else state.cursor_id,
            'schema_sample': self._schema_sample(encoded),
            'validator': copy.deepcopy(
                target.get('options', {}).get('validator')
            ),
            'immutable_fields': ['_id'],
            'builders': {
                'filter': True, 'projection': True, 'sort': True,
                'unset': True, 'bulk_preview': True, 'nested_diff': True,
            },
        }

    @staticmethod
    def _schema_sample(documents):
        fields = {}

        def visit(value, prefix=''):
            if not isinstance(value, Mapping):
                return
            for name, item in value.items():
                path = f'{prefix}.{name}' if prefix else str(name)
                entry = fields.setdefault(path, {
                    'path': path, 'present_count': 0, 'types': set(),
                })
                entry['present_count'] += 1
                if item is None:
                    kind = 'null'
                elif isinstance(item, bool):
                    kind = 'boolean'
                elif isinstance(item, (int, float)):
                    kind = 'number'
                elif isinstance(item, str):
                    kind = 'string'
                elif isinstance(item, list):
                    kind = 'array'
                elif isinstance(item, Mapping):
                    kind = 'object'
                else:
                    kind = type(item).__name__
                entry['types'].add(kind)
                visit(item, path)

        for document in documents:
            visit(document)
        count = len(documents)
        result = []
        for path in sorted(fields):
            item = fields[path]
            result.append({
                'path': path,
                'present_count': item['present_count'],
                'missing_count': count - item['present_count'],
                'types': sorted(item['types']),
                'optional': item['present_count'] != count,
            })
        return {'sample_size': count, 'fields': result}

    def cancel_admin_cursor(self, request):
        cursor_id = _identifier(
            request.get('continuation'), 'document continuation'
        )
        state = self._admin_cursors.pop(cursor_id, None)
        if state is None:
            return {'cancelled': False}
        state.close()
        return {'cancelled': True}

    def complete(self, _request):
        return []

    def close(self):
        for session in tuple(self._sessions):
            try:
                session.close()
            except Exception:
                pass
        self._sessions.clear()
        for client in tuple(self._clients):
            self._forget_client(client)
        for result in self._results:
            result.close_cursor()
        self._results.clear()
        for state in self._admin_cursors.values():
            state.close()
        self._admin_cursors.clear()
