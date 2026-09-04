##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Neo4j Bolt boundary for the Neo4j 2026.04.0 semantic provider.

The adapter owns Cypher and graph semantics. Driver transaction observations
remain opaque: common CDEadmin code must never infer finality or retry policy.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from dataclasses import dataclass, field
from contextlib import ExitStack
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

from pgadmin.cdeadmin.sdk import (
    PilotProviderError,
    ProviderToolError,
    ProviderToolGrant,
    ProviderToolRunner,
)


MAX_RESULT_RECORDS = 10000
MAX_PAGE_SIZE = 500
MAX_QUERY_BYTES = 2 * 1024 * 1024
MAX_PROPERTIES = 10000
QUALIFIED_DRIVER_VERSION = '6.3.0'
QUALIFIED_GDS_VERSION = '2026.04.0'
QUALIFIED_GDS_SHA256 = (
    '9b715ca68abe64aa55fa77461fe8f0b25c64398349ed139177484c42d1482cdf'
)


class Neo4jClientError(PilotProviderError):
    """A Neo4j dependency or native operation failed safely."""


class Neo4jDependencyError(Neo4jClientError):
    """The selected Neo4j Python driver is unavailable."""


@dataclass
class _Neo4jSession:
    driver: object
    session: object
    database: str
    closed: bool = False

    def close(self):
        if self.closed:
            return
        try:
            self.session.close()
        finally:
            self.driver.close()
            self.closed = True


@dataclass
class _Neo4jResult:
    result: object
    session: _Neo4jSession
    fields: list[str]
    records: list[dict[str, Any]] = field(default_factory=list)
    emitted: int = 0
    complete: bool = False
    cancelled: bool = False
    summary: dict[str, Any] | None = None


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise Neo4jClientError(f'{label} must be an object')
    return copy.deepcopy(dict(value))


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Neo4jClientError(f'{label} must not be empty')
    value = value.strip()
    if len(value) > 1024 or any(ord(char) < 32 for char in value):
        raise Neo4jClientError(f'{label} contains forbidden characters')
    return value


def _quoted(value: object, label='identifier') -> str:
    return '`' + _identifier(value, label).replace('`', '``') + '`'


def _bounded_int(value, default, minimum, maximum, label):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise Neo4jClientError(f'{label} must be an integer')
    if not minimum <= value <= maximum:
        raise Neo4jClientError(
            f'{label} must be between {minimum} and {maximum}'
        )
    return value


def _record_data(record):
    # Neo4j ``Record.data()`` is a convenience export that recursively turns
    # Node and Relationship values into plain property dictionaries. Preserve
    # the native values so element IDs, labels and relationship endpoints
    # remain available to graph editors and result renderers.
    if isinstance(record, Mapping):
        return dict(record)
    data = getattr(record, 'data', None)
    if callable(data):
        return data()
    return {'value': record}


class Neo4jClient:
    """Synchronous, bounded port over the official Neo4j Python driver."""

    ROUTE_KEYS = frozenset({
        'route_id', 'host', 'port', 'database', 'username', 'user',
        'principal_reference', 'credential_reference_id', 'routing',
        'tls_mode', 'connection_timeout', 'connection_acquisition_timeout',
        'max_transaction_retry_time', 'max_connection_pool_size',
        'max_connection_lifetime', 'keep_alive', 'user_agent',
        'tool_workspace', 'auth_mode', 'auth_realm', 'auth_scheme',
        'auth_parameters', 'credential_kinds', 'credential_references',
        'client_certificate', 'tls_certificate_file', 'tls_key_file',
        'liveness_check_timeout', 'resolver_addresses',
        'notifications_min_severity', 'access_mode', 'fetch_size',
        'impersonated_user', 'bookmarks',
    })

    ADMIN_OPERATIONS = {
        'dbms': frozenset({'inspect', 'execute'}),
        'server': frozenset({'inspect', 'alter', 'execute'}),
        'database': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'composite-database': frozenset({
            'inspect', 'create', 'drop',
        }),
        'alias': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'graph': frozenset({'inspect', 'insert'}),
        'node': frozenset({'inspect', 'insert', 'update', 'delete'}),
        'label': frozenset({'inspect'}),
        'relationship': frozenset({'inspect', 'insert', 'update', 'delete'}),
        'relationship-type': frozenset({'inspect'}),
        'property': frozenset({'inspect'}),
        'index': frozenset({'inspect', 'create', 'drop'}),
        'constraint': frozenset({'inspect', 'create', 'drop'}),
        'procedure': frozenset({'inspect', 'execute'}),
        'function': frozenset({'inspect'}),
        'setting': frozenset({'inspect'}),
        'transaction': frozenset({'inspect', 'execute'}),
        'query': frozenset({'inspect', 'execute'}),
        'query-plan': frozenset({'inspect', 'execute'}),
        'graph-projection': frozenset({'inspect', 'create', 'drop'}),
        'user': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
        'role': frozenset({
            'inspect', 'create', 'rename', 'grant', 'revoke', 'drop',
        }),
        'privilege': frozenset({'inspect', 'grant', 'revoke'}),
        'backup': frozenset({'inspect', 'execute'}),
        'restore': frozenset({'inspect', 'execute'}),
        'import': frozenset({'inspect', 'execute'}),
        'export': frozenset({'inspect', 'execute'}),
        'shell': frozenset({'inspect', 'execute'}),
        'consistency-check': frozenset({'inspect', 'execute'}),
    }

    def __init__(
        self, secret_acquirer=None, module=None, gds_surface_sha256=None,
    ):
        try:
            self.module = module or importlib.import_module('neo4j')
        except (ImportError, ModuleNotFoundError) as exc:
            raise Neo4jDependencyError(
                'Neo4j client dependency neo4j is unavailable'
            ) from exc
        graph_database = getattr(self.module, 'GraphDatabase', None)
        connector = getattr(graph_database, 'driver', None)
        if not callable(connector):
            raise Neo4jDependencyError(
                'Neo4j Python driver has no approved GraphDatabase.driver'
            )
        observed_version = getattr(self.module, '__version__', None)
        if observed_version is not None and str(
            observed_version
        ) != QUALIFIED_DRIVER_VERSION:
            raise Neo4jDependencyError(
                'Neo4j Python driver version is not the qualified 6.3.0'
            )
        self._connector = connector
        self._secret_acquirer = secret_acquirer
        if gds_surface_sha256 is not None and (
                not isinstance(gds_surface_sha256, str) or
                gds_surface_sha256.lower() != QUALIFIED_GDS_SHA256):
            raise Neo4jClientError(
                'GDS external-surface digest does not match the qualified '
                '2026.04.0 artifact'
            )
        self._gds_surface_sha256 = gds_surface_sha256
        self._drivers: list[object] = []
        self._sessions: list[_Neo4jSession] = []
        self._results: list[_Neo4jResult] = []
        self._tool_runner = ProviderToolRunner({
            'cypher-shell': 'cypher-shell',
            'neo4j-admin-backup': 'neo4j-admin',
            'neo4j-admin-restore': 'neo4j-admin',
            'neo4j-admin-import': 'neo4j-admin',
            'neo4j-admin-dump': 'neo4j-admin',
            'neo4j-admin-check': 'neo4j-admin',
        })

    @classmethod
    def _route(cls, request):
        route = _mapping(
            request.get('route', request.get('_provider_route')),
            'Neo4j route',
        )
        unknown = sorted(set(route).difference(cls.ROUTE_KEYS))
        if unknown:
            raise Neo4jClientError(
                'Neo4j route contains unknown fields: ' + ', '.join(unknown)
            )
        if route.get('user') is not None:
            if route.get('username') not in {None, route['user']}:
                raise Neo4jClientError('Neo4j route user aliases disagree')
            route['username'] = route.pop('user')
        _identifier(route.get('host'), 'Neo4j host')
        _bounded_int(route.get('port'), 7687, 1, 65535, 'Neo4j port')
        tls_mode = route.get('tls_mode', 'disabled')
        if tls_mode not in {'disabled', 'system-ca', 'self-signed'}:
            raise Neo4jClientError('Neo4j TLS mode is invalid')
        route['tls_mode'] = tls_mode
        for name in ('routing', 'keep_alive'):
            if route.get(name) is not None and not isinstance(
                route[name], bool
            ):
                raise Neo4jClientError(
                    f'Neo4j route {name} must be true or false'
                )
        auth_mode = route.get(
            'auth_mode', 'basic' if route.get('username') else 'none'
        )
        if auth_mode not in {'none', 'basic', 'kerberos', 'bearer', 'custom'}:
            raise Neo4jClientError('Neo4j authentication mode is invalid')
        route['auth_mode'] = auth_mode
        references = route.get('credential_references') or {}
        if not isinstance(references, Mapping):
            raise Neo4jClientError(
                'Neo4j credential references must be an object'
            )
        if route.get('credential_reference_id') is not None:
            references.setdefault(
                'database_password', route['credential_reference_id']
            )
        route['credential_references'] = dict(references)
        required_kind = {
            'basic': 'database_password',
            'kerberos': 'authentication_token',
            'bearer': 'authentication_token',
            'custom': 'custom_auth_credentials',
        }.get(auth_mode)
        if required_kind and required_kind not in references:
            raise Neo4jClientError(
                'Neo4j selected authentication credential is unavailable'
            )
        if auth_mode in {'basic', 'custom'} and not route.get('username'):
            raise Neo4jClientError(
                'Neo4j selected authentication requires a username'
            )
        if auth_mode not in {'basic', 'custom'} and route.get('username'):
            raise Neo4jClientError(
                'Neo4j username is not valid for this authentication mode'
            )
        if route.get('auth_parameters') is not None and not isinstance(
            route['auth_parameters'], Mapping
        ):
            raise Neo4jClientError(
                'Neo4j custom authentication parameters must be an object'
            )
        if auth_mode == 'custom' and not route.get('auth_scheme'):
            raise Neo4jClientError(
                'Neo4j custom authentication requires a scheme'
            )
        client_certificate = route.get('client_certificate', False)
        if not isinstance(client_certificate, bool):
            raise Neo4jClientError(
                'Neo4j client certificate setting must be true or false'
            )
        route['client_certificate'] = client_certificate
        for name in ('tls_certificate_file', 'tls_key_file'):
            value = route.get(name)
            if value is not None:
                path = Path(_identifier(value, f'Neo4j {name}'))
                if not path.is_absolute():
                    raise Neo4jClientError(f'Neo4j {name} must be absolute')
                route[name] = str(path.resolve(strict=False))
        if client_certificate and (
            route['tls_mode'] == 'disabled' or
            not route.get('tls_certificate_file') or
            not route.get('tls_key_file')
        ):
            raise Neo4jClientError(
                'Neo4j client certificate requires TLS, certificate, and key'
            )
        addresses = route.get('resolver_addresses')
        if addresses is not None and (
            not isinstance(addresses, list) or not all(
                isinstance(item, str) and item.strip() for item in addresses
            )
        ):
            raise Neo4jClientError(
                'Neo4j resolver addresses must be a string array'
            )
        access_mode = route.get('access_mode', 'write')
        if access_mode not in {'read', 'write'}:
            raise Neo4jClientError('Neo4j access mode is invalid')
        route['access_mode'] = access_mode
        route['fetch_size'] = _bounded_int(
            route.get('fetch_size'), 1000, 1, 1000000, 'fetch size'
        )
        if route.get('impersonated_user'):
            route['impersonated_user'] = _identifier(
                route['impersonated_user'], 'Neo4j impersonated user'
            )
        bookmarks = route.get('bookmarks', [])
        if not isinstance(bookmarks, list) or not all(
            isinstance(item, str) and item.strip() for item in bookmarks
        ):
            raise Neo4jClientError('Neo4j bookmarks must be a string array')
        route['bookmarks'] = [item.strip() for item in bookmarks]
        return route

    @staticmethod
    def _uri(route):
        scheme = 'neo4j' if route.get('routing', True) else 'bolt'
        suffix = {'disabled': '', 'system-ca': '+s', 'self-signed': '+ssc'}[
            route['tls_mode']
        ]
        host = route['host']
        if ':' in host and not host.startswith('['):
            host = f'[{host}]'
        return (
            f'{scheme}{suffix}://{host}:'
            f'{_bounded_int(route.get("port"), 7687, 1, 65535, "port")}'
        )

    def _connector_options(self, route, credentials=None):
        credentials = credentials or {}
        result = {
            'connection_timeout': float(_bounded_int(
                route.get('connection_timeout'), 30, 1, 600,
                'connection timeout',
            )),
            'connection_acquisition_timeout': float(_bounded_int(
                route.get('connection_acquisition_timeout'), 60, 1, 3600,
                'connection acquisition timeout',
            )),
            'max_transaction_retry_time': float(_bounded_int(
                route.get('max_transaction_retry_time'), 30, 0, 3600,
                'maximum transaction retry time',
            )),
            'max_connection_pool_size': _bounded_int(
                route.get('max_connection_pool_size'), 100, 1, 10000,
                'maximum connection pool size',
            ),
            'max_connection_lifetime': float(_bounded_int(
                route.get('max_connection_lifetime'), 3600, 1, 86400,
                'maximum connection lifetime',
            )),
            'keep_alive': route.get('keep_alive', True) is not False,
            'liveness_check_timeout': float(_bounded_int(
                route.get('liveness_check_timeout'), 0, 0, 86400,
                'liveness-check timeout',
            )),
        }
        if route.get('user_agent'):
            result['user_agent'] = _identifier(
                route['user_agent'], 'Neo4j user agent'
            )
        severity = route.get('notifications_min_severity', 'INFORMATION')
        if severity not in {'OFF', 'INFORMATION', 'WARNING'}:
            raise Neo4jClientError(
                'Neo4j notification severity is invalid'
            )
        result['notifications_min_severity'] = severity
        addresses = route.get('resolver_addresses')
        if addresses:
            allowed = tuple(addresses)

            def resolver(_address):
                return allowed

            result['resolver'] = resolver
        if route.get('client_certificate'):
            try:
                management = importlib.import_module('neo4j.auth_management')
                certificate = management.ClientCertificate(
                    route['tls_certificate_file'], route['tls_key_file'],
                    credentials.get('tls_private_key_password'),
                )
                result['client_certificate'] = (
                    management.ClientCertificateProviders.static(certificate)
                )
            except (AttributeError, ImportError, ModuleNotFoundError) as exc:
                raise Neo4jDependencyError(
                    'Neo4j driver lacks client-certificate support'
                ) from exc
        return result

    def _auth(self, route, credentials):
        mode = route['auth_mode']
        if mode == 'none':
            return None
        if mode == 'basic':
            factory = getattr(self.module, 'basic_auth', None)
            if not callable(factory):
                return (route['username'], credentials['database_password'])
            return factory(route['username'], credentials['database_password'],
                           route.get('auth_realm'))
        if mode == 'kerberos':
            return self._auth_factory('kerberos_auth')(
                credentials['authentication_token']
            )
        if mode == 'bearer':
            return self._auth_factory('bearer_auth')(
                credentials['authentication_token']
            )
        return self._auth_factory('custom_auth')(
            route['username'], credentials['custom_auth_credentials'],
            route.get('auth_realm'), route['auth_scheme'],
            **dict(route.get('auth_parameters') or {}),
        )

    def _auth_factory(self, name):
        factory = getattr(self.module, name, None)
        if not callable(factory):
            raise Neo4jDependencyError(
                f'Neo4j driver lacks {name} authentication support'
            )
        return factory

    def _connect(self, request):
        route = self._route(request)
        references = route.get('credential_references', {})
        principal = route.get('principal_reference')
        uri = self._uri(route)

        def connect(credentials=None):
            credentials = credentials or {}
            auth = self._auth(route, credentials)
            options = self._connector_options(route, credentials)
            driver = self._connector(uri, auth=auth, **options)
            self._drivers.append(driver)
            return driver

        if not references:
            return connect(), route
        _identifier(principal, 'principal reference')
        if not callable(self._secret_acquirer):
            raise Neo4jClientError('Neo4j credential binding is unavailable')
        credentials = {}
        with ExitStack() as stack:
            for kind, reference in sorted(references.items()):
                if kind not in {
                    'database_password', 'authentication_token',
                    'custom_auth_credentials', 'tls_private_key_password',
                }:
                    raise Neo4jClientError(
                        'Neo4j credential kind is unsupported'
                    )
                lease = stack.enter_context(self._secret_acquirer(
                    _identifier(reference, 'credential reference'),
                    principal, 'connect', kind,
                ))
                credentials[kind] = lease.use(
                    lambda value: bytes(value).decode('utf-8')
                )
            driver = connect(credentials)
        return driver, route

    def _forget_driver(self, driver):
        try:
            driver.close()
        finally:
            if driver in self._drivers:
                self._drivers.remove(driver)

    def _session(self, driver, route, database=None):
        options = {
            'database': database or route.get('database'),
            'fetch_size': route['fetch_size'],
            'default_access_mode': getattr(
                self.module,
                'READ_ACCESS' if route['access_mode'] == 'read'
                else 'WRITE_ACCESS',
                route['access_mode'].upper(),
            ),
        }
        if route.get('impersonated_user'):
            options['impersonated_user'] = route['impersonated_user']
        if route['bookmarks']:
            bookmark_type = getattr(self.module, 'Bookmarks', None)
            factory = getattr(bookmark_type, 'from_raw_values', None)
            options['bookmarks'] = (
                factory(route['bookmarks']) if callable(factory)
                else route['bookmarks']
            )
        return driver.session(**options)

    def runtime_identity(self, request, handle=None):
        owned = handle is None
        driver = None
        session = None
        try:
            if handle is not None:
                driver, session = handle.driver, handle.session
            else:
                driver, route = self._connect(request)
                verify = getattr(driver, 'verify_connectivity', None)
                if callable(verify):
                    verify()
                session = self._session(driver, route, 'system')
            result = session.run(
                'CALL dbms.components() YIELD name, versions, edition '
                'RETURN name, versions, edition'
            )
            rows = [_record_data(record) for record in result]
            row = next(
                (
                    record for record in rows
                    if record.get('name') == 'Neo4j Kernel'
                ),
                rows[0] if rows else {},
            )
            versions = row.get('versions') or []
            version = str(versions[0] if versions else '')
            if not version:
                raise Neo4jClientError('Neo4j runtime omitted its version')
            server_info = None
            getter = getattr(driver, 'get_server_info', None)
            if callable(getter):
                server_info = getter()
            agent = str(getattr(
                server_info, 'agent', row.get('name', 'Neo4j')
            ))
            protocol = getattr(server_info, 'protocol_version', None)
            build = f'{agent}:{row.get("edition", "unknown")}'
            return {
                'engine_id': 'neo4j', 'version': version,
                'build_id': build, 'protocol_id': 'bolt',
                'native': {
                    'component': row.get('name'),
                    'edition': row.get('edition'),
                    'protocol_version': str(protocol) if protocol else None,
                },
            }
        except Neo4jClientError:
            raise
        except Exception as exc:
            raise Neo4jClientError(
                f'Neo4j runtime identity failed ({type(exc).__name__})'
            ) from None
        finally:
            if owned:
                if session is not None:
                    session.close()
                if driver is not None:
                    self._forget_driver(driver)

    def open_session(self, request):
        try:
            driver, route = self._connect(request)
            native = self._session(driver, route)
            handle = _Neo4jSession(
                driver, native, str(route.get('database') or 'neo4j')
            )
            self._sessions.append(handle)
            return handle
        except Exception as exc:
            if isinstance(exc, Neo4jClientError):
                raise
            raise Neo4jClientError(
                f'Neo4j session open failed ({type(exc).__name__})'
            ) from None

    @staticmethod
    def describe_transaction(handle):
        return {
            'native_boundary': 'neo4j-bolt-session',
            'database': handle.database,
            'explicit_transaction_exposed': False,
            'auto_commit_query_semantics': 'driver-and-server-owned',
            'common_finality_inference': False,
            'retry_decision_owned_by_common_code': False,
        }

    def execute(self, handle, request):
        source = request.get('source')
        if not isinstance(source, str) or not source.strip():
            raise Neo4jClientError('Cypher source must not be empty')
        if len(source.encode('utf-8')) > MAX_QUERY_BYTES:
            raise Neo4jClientError('Cypher source exceeds the safety limit')
        parameters = request.get('parameters', {})
        parameters = _mapping(parameters, 'Cypher parameters')
        try:
            result = handle.session.run(source, parameters)
            fields = list(result.keys())
            token = _Neo4jResult(result, handle, fields)
            self._results.append(token)
            return token
        except Exception as exc:
            raise Neo4jClientError(
                f'Neo4j execution failed ({type(exc).__name__})'
            ) from None

    def cancel(self, token):
        if token.complete or token.cancelled:
            return False
        cancel = getattr(token.result, 'cancel', None)
        if callable(cancel):
            cancel()
        token.cancelled = True
        token.complete = True
        return True

    def describe_result(self, token):
        if not token.complete:
            remaining = MAX_RESULT_RECORDS - token.emitted
            amount = min(MAX_PAGE_SIZE, remaining)
            fetched = token.result.fetch(amount) if amount else []
            token.records = [
                self._json_value(_record_data(record)) for record in fetched
            ]
            token.emitted += len(token.records)
            if len(fetched) < amount or token.emitted >= MAX_RESULT_RECORDS:
                summary = token.result.consume()
                token.summary = self._summary(summary)
                token.complete = True
        return {
            'result_kind': 'graph',
            'schema': {
                'columns': [
                    {'name': name, 'type': 'neo4j-value'}
                    for name in token.fields
                ],
                'summary': copy.deepcopy(token.summary),
            },
            'complete': token.complete,
            'stream_reference': (
                None if token.complete else 'provider-retained'
            ),
            'payload': {'graphs': copy.deepcopy(token.records)},
        }

    @classmethod
    def _summary(cls, summary):
        if summary is None:
            return {}
        counters = getattr(summary, 'counters', None)
        statistics = {}
        if counters is not None:
            for name in (
                'nodes_created', 'nodes_deleted', 'relationships_created',
                'relationships_deleted', 'properties_set', 'labels_added',
                'labels_removed', 'indexes_added', 'indexes_removed',
                'constraints_added', 'constraints_removed',
            ):
                value = getattr(counters, name, None)
                if value is not None:
                    statistics[name] = value
        plan = getattr(summary, 'profile', None) or getattr(
            summary, 'plan', None
        )
        return {
            'query_type': str(getattr(summary, 'query_type', '')),
            'result_available_after_ms': getattr(
                summary, 'result_available_after', None
            ),
            'result_consumed_after_ms': getattr(
                summary, 'result_consumed_after', None
            ),
            'counters': statistics,
            'query_plan': cls._query_plan(plan),
            'driver_observation_only': True,
            'common_finality_inference': False,
        }

    @classmethod
    def _query_plan(cls, plan, depth=0, budget=None):
        if plan is None:
            return None
        if budget is None:
            budget = [1000]
        if depth >= 64 or budget[0] <= 0:
            return {'truncated': True}
        budget[0] -= 1
        children = getattr(plan, 'children', ()) or ()
        return {
            'operator_type': str(getattr(plan, 'operator_type', '')),
            'identifiers': sorted(
                str(item) for item in (
                    getattr(plan, 'identifiers', ()) or ()
                )
            ),
            'arguments': cls._json_value(
                getattr(plan, 'arguments', {}) or {}
            ),
            'children': [
                cls._query_plan(child, depth + 1, budget)
                for child in children
                if budget[0] > 0
            ],
        }

    @classmethod
    def _json_value(cls, value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (datetime, date, time, timedelta, Decimal)):
            return str(value)
        nodes = getattr(value, 'nodes', None)
        relationships = getattr(value, 'relationships', None)
        if nodes is not None and relationships is not None:
            return {
                'kind': 'path',
                'nodes': [cls._json_value(item) for item in nodes],
                'relationships': [
                    cls._json_value(item) for item in relationships
                ],
            }
        labels = getattr(value, 'labels', None)
        element_id = getattr(value, 'element_id', None)
        if labels is not None and element_id is not None:
            return {
                'kind': 'node', 'element_id': str(element_id),
                'labels': sorted(str(item) for item in labels),
                'properties': cls._json_value(dict(value)),
            }
        rel_type = getattr(value, 'type', None)
        if rel_type is not None and element_id is not None:
            start = getattr(value, 'start_node', None)
            end = getattr(value, 'end_node', None)
            return {
                'kind': 'relationship', 'element_id': str(element_id),
                'type': str(rel_type),
                'start_node_element_id': str(getattr(start, 'element_id', '')),
                'end_node_element_id': str(getattr(end, 'element_id', '')),
                'properties': cls._json_value(dict(value)),
            }
        if isinstance(value, Mapping):
            return {
                str(key): cls._json_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [cls._json_value(item) for item in value]
        x = getattr(value, 'x', None)
        y = getattr(value, 'y', None)
        if x is not None and y is not None:
            result = {'kind': 'point', 'x': x, 'y': y}
            for name in ('z', 'srid'):
                item = getattr(value, name, None)
                if item is not None:
                    result[name] = item
            return result
        return {'kind': 'driver-value', 'value': str(value)}

    def _read(self, request, statement, parameters=None, database=None):
        driver, route = self._connect(request)
        session = None
        try:
            session = self._session(driver, route, database)
            return self._rows(session, statement, parameters)
        finally:
            if session is not None:
                session.close()
            self._forget_driver(driver)

    def _rows(self, session, statement, parameters=None):
        return [
            self._json_value(_record_data(record))
            for record in session.run(statement, parameters or {})
        ]

    @staticmethod
    def _resource(kind, name, native, parent=None):
        identity = str(native.get('element_id') or native.get('name') or name)
        encoded = quote(identity, safe='')
        generation = hashlib.sha256(
            json.dumps(native, sort_keys=True, default=str).encode('utf-8')
        ).hexdigest()[:20]
        path = ['neo4j', kind, name]
        if parent:
            path = ['neo4j', parent, kind, name]
        return {
            'resource_id': f'neo4j:{kind}:{encoded}',
            'resource_kind': kind, 'display_name': str(name),
            'display_path': path, 'authority_path': path,
            'generation': generation, 'native': copy.deepcopy(native),
        }

    DISCOVERY = (
        ('database', 'SHOW DATABASES YIELD * RETURN *', 'name'),
        ('server', 'SHOW SERVERS YIELD * RETURN *', 'name'),
        ('alias', 'SHOW ALIASES FOR DATABASES YIELD * RETURN *', 'name'),
        ('label', 'CALL db.labels() YIELD label RETURN label', 'label'),
        ('relationship-type',
         'CALL db.relationshipTypes() YIELD relationshipType '
         'RETURN relationshipType', 'relationshipType'),
        ('property',
         'CALL db.propertyKeys() YIELD propertyKey RETURN propertyKey',
         'propertyKey'),
        ('index', 'SHOW INDEXES YIELD * RETURN *', 'name'),
        ('constraint', 'SHOW CONSTRAINTS YIELD * RETURN *', 'name'),
        ('procedure', 'SHOW PROCEDURES YIELD * RETURN *', 'name'),
        ('function', 'SHOW FUNCTIONS YIELD * RETURN *', 'name'),
        ('setting', 'SHOW SETTINGS YIELD * RETURN *', 'name'),
        ('transaction', 'SHOW TRANSACTIONS YIELD * RETURN *', 'transactionId'),
        ('user', 'SHOW USERS YIELD * RETURN *', 'user'),
        ('role', 'SHOW ROLES YIELD * RETURN *', 'role'),
        ('privilege', 'SHOW PRIVILEGES YIELD * RETURN *', 'action'),
    )

    def list_resources(self, request):
        resources = [self._resource(
            'dbms', 'Neo4j DBMS', {'name': 'Neo4j DBMS'},
        )]
        driver, route = self._connect(request)
        system = None
        graph = None
        try:
            system = self._session(driver, route, 'system')
            graph = self._session(driver, route)
            for kind, statement, name_field in self.DISCOVERY:
                session = system if kind in {
                    'database', 'server', 'alias', 'user', 'role',
                    'privilege',
                } else graph
                try:
                    rows = self._rows(session, statement)
                except Exception:
                    # Edition- or privilege-dependent categories do not
                    # suppress other discoverable resources.
                    continue
                for row in rows[:2000]:
                    name = row.get(name_field)
                    if name is None:
                        continue
                    actual_kind = kind
                    is_composite = (
                        kind == 'database' and
                        str(row.get('type', '')).lower() == 'composite'
                    )
                    if is_composite:
                        actual_kind = 'composite-database'
                    resources.append(self._resource(
                        actual_kind, str(name), row
                    ))
                    if kind == 'transaction' and row.get('currentQuery'):
                        query_name = str(
                            row.get('currentQueryId') or name
                        )
                        resources.append(self._resource(
                            'query', query_name, row
                        ))
            graph_rows = self._rows(
                graph,
                'MATCH (n) OPTIONAL MATCH (n)-[r]->(m) '
                'RETURN n, r, m LIMIT 500',
            )
            seen_nodes = set()
            seen_relationships = set()
            for row in graph_rows:
                for field in ('n', 'm'):
                    node = row.get(field)
                    if not isinstance(node, Mapping) or node.get(
                            'kind') != 'node':
                        continue
                    element_id = str(node.get('element_id'))
                    if element_id in seen_nodes:
                        continue
                    seen_nodes.add(element_id)
                    resources.append(self._resource(
                        'node', element_id, node
                    ))
                relationship = row.get('r')
                if not isinstance(relationship, Mapping) or relationship.get(
                        'kind') != 'relationship':
                    continue
                element_id = str(relationship.get('element_id'))
                if element_id in seen_relationships:
                    continue
                seen_relationships.add(element_id)
                resources.append(self._resource(
                    'relationship', element_id, relationship
                ))
            try:
                projections = self._rows(
                    graph, 'CALL gds.graph.list()'
                )
            except Exception:
                projections = []
            for projection in projections[:2000]:
                name = projection.get('graphName')
                if name is not None:
                    resources.append(self._resource(
                        'graph-projection', str(name), projection
                    ))
        finally:
            if graph is not None:
                graph.close()
            if system is not None:
                system.close()
            self._forget_driver(driver)
        database = route.get('database') or 'neo4j'
        resources.append(self._resource(
            'graph', database, {'name': database, 'database': database},
        ))
        plan_workspace = self._resource(
            'query-plan', 'Cypher query plans', {
                'name': 'Cypher query plans', 'database': database,
                'workspace': True,
            },
        )
        plan_workspace['is_virtual'] = True
        resources.append(plan_workspace)
        for kind, name in (
            ('backup', 'Database backup'),
            ('restore', 'Database restore'),
            ('import', 'Offline bulk import'),
            ('export', 'Database dump'),
            ('shell', 'Cypher shell'),
            ('consistency-check', 'Database consistency check'),
        ):
            resource = self._resource(
                kind, name, {'name': name, 'tool_resource': True}
            )
            resource['is_virtual'] = True
            resources.append(resource)
        return resources

    def inspect_resource(self, request):
        requested = request.get('resource_id')
        for resource in self.list_resources(request):
            if resource['resource_id'] == requested:
                return resource
        raise Neo4jClientError('Neo4j resource is unavailable')

    def describe_security(self, request):
        users = []
        roles = []
        privileges = []
        driver, route = self._connect(request)
        session = None
        try:
            session = self._session(driver, route, 'system')
            for kind, target in (
                ('user', users), ('role', roles), ('privilege', privileges)
            ):
                descriptor = next(
                    item for item in self.DISCOVERY if item[0] == kind
                )
                try:
                    target.extend(self._rows(
                        session, descriptor[1]
                    )[:2000])
                except Exception:
                    pass
        finally:
            if session is not None:
                session.close()
            self._forget_driver(driver)
        return {
            'resource_id': 'neo4j:security:current',
            'display_name': 'Neo4j authorization',
            'authority_path': ['neo4j', 'security', 'current'],
            'generation': hashlib.sha256(json.dumps(
                [users, roles, privileges], sort_keys=True, default=str
            ).encode()).hexdigest()[:20],
            'native': {
                'authorization_model': 'neo4j-native-rbac',
                'users': users, 'roles': roles, 'privileges': privileges,
            },
        }

    def supports_admin_operation(self, resource_kind, operation_id):
        if resource_kind == 'graph-projection' and not getattr(
                self, '_gds_surface_sha256', None):
            return False
        return operation_id in self.ADMIN_OPERATIONS.get(
            resource_kind, frozenset()
        )

    def visual_admin_catalog(self, catalog):
        catalog['native_planner'] = 'neo4j-cypher-structured-planner'
        catalog['query_language'] = 'cypher'
        catalog['graph_identity'] = 'elementId'
        catalog['transaction_authority'] = 'neo4j-driver-and-server'
        catalog['experience_families'] = ['graph']

        def declaration(*resource_kinds, status='supported', reason=None):
            return {
                'status': status,
                'resource_kinds': list(resource_kinds),
                'operation_obligations': {
                    kind: sorted(self.ADMIN_OPERATIONS[kind])
                    for kind in resource_kinds
                },
                'reason': reason or (
                    'Neo4j graph objects and operations are provider-owned '
                    'through the Bolt/Cypher surface.'
                ),
                'evidence': ['neo4j-2026.04-native-bolt-catalog'],
            }

        catalog['concept_declarations'] = {'graph': {
            'databases': declaration('database'),
            'nodes': declaration('node'),
            'relationships': declaration('relationship'),
            'labels': declaration('label', status='read_only'),
            'constraints': declaration('constraint'),
            'indexes': declaration('index'),
            'procedures': declaration('procedure'),
            'graph_projections': {
                'status': 'supported',
                'resource_kinds': ['graph-projection'],
                'operation_obligations': {
                    'graph-projection': sorted(
                        self.ADMIN_OPERATIONS['graph-projection']
                    ),
                },
                'external_surface': 'neo4j-graph-data-science-plugin',
                'external_surface_digest_required': True,
                'reason': (
                    'Graph projections belong to the separately versioned '
                    'Neo4j Graph Data Science plugin. The base provider keeps '
                    'the workspace visible but blocked until a plugin profile '
                    'and live surface digest are qualified.'
                ),
                'evidence': ['neo4j-gds-external-surface-required'],
            },
            'transactions': declaration('transaction'),
            'query_plans': declaration('query-plan'),
            'cluster_members': declaration('server'),
        }}
        for resource in catalog.get('objects', []):
            kind = resource['resource_kind']
            for operation in resource.get('operations', []):
                if kind in {'node', 'relationship'} and operation[
                    'operation_id'
                ] == 'insert':
                    operation['target_required'] = True
                    operation['target_resource_kinds'] = ['graph']
                if kind in {
                    'backup', 'restore', 'import', 'export', 'shell',
                    'consistency-check',
                }:
                    operation['required_permissions'] = [
                        'filesystem', 'execute',
                    ]
                if kind in {'database', 'composite-database'} and operation[
                    'operation_id'
                ] == 'drop':
                    operation['form'] = {
                        'form_id': 'neo4j-database-drop',
                        'title': 'Drop Neo4j database',
                        'fields': [{
                            'field_id': 'data_disposition',
                            'label': 'Data disposition', 'control': 'select',
                            'required': True, 'default': 'destroy',
                            'options': [
                                {'value': 'destroy', 'label': 'Destroy data'},
                                {'value': 'dump', 'label': 'Dump data'},
                            ],
                        }, {
                            'field_id': 'alias_action',
                            'label': 'Database aliases', 'control': 'select',
                            'required': True, 'default': 'restrict',
                            'options': [
                                {'value': 'restrict', 'label': 'Restrict'},
                                {
                                    'value': 'cascade',
                                    'label': 'Cascade aliases',
                                },
                            ],
                        }, {
                            'field_id': 'wait_seconds',
                            'label': 'Wait seconds', 'control': 'number',
                            'required': False,
                        }, {
                            'field_id': 'confirmation',
                            'label': 'Confirmation', 'control': 'text',
                            'required': True,
                        }],
                    }
                if kind == 'query-plan':
                    operation['form'] = {
                        'form_id': 'neo4j-query-plan',
                        'title': 'Neo4j query plan',
                        'fields': ([{
                            'field_id': 'source',
                            'label': 'Cypher query', 'control': 'code',
                            'required': True,
                            'max_length': MAX_QUERY_BYTES,
                        }, {
                            'field_id': 'parameters',
                            'label': 'Query parameters', 'control': 'json',
                            'required': False, 'default': {},
                        }, {
                            'field_id': 'mode',
                            'label': 'Plan mode', 'control': 'select',
                            'required': True, 'default': 'explain',
                            'options': [
                                {'value': 'explain', 'label': 'Explain'},
                                {'value': 'profile', 'label': 'Profile'},
                            ],
                        }] if operation['operation_id'] == 'execute' else [])
                    }
                if kind == 'procedure' and operation[
                        'operation_id'] == 'execute':
                    operation['form'] = {
                        'form_id': 'neo4j-procedure-execute',
                        'title': 'Run Neo4j procedure',
                        'fields': [{
                            'field_id': 'action', 'label': 'Action',
                            'control': 'select', 'required': True,
                            'default': 'execute', 'options': [{
                                'value': 'execute', 'label': 'Execute',
                            }],
                        }, {
                            'field_id': 'arguments',
                            'label': 'Positional arguments',
                            'control': 'json', 'required': False,
                            'default': [],
                        }],
                    }
                if kind == 'graph-projection' and operation[
                        'operation_id'] == 'create':
                    operation['form'] = {
                        'form_id': 'neo4j-gds-graph-project',
                        'title': 'Create GDS graph projection',
                        'fields': [{
                            'field_id': 'name', 'label': 'Graph name',
                            'control': 'text', 'required': True,
                            'max_length': 1024,
                        }, {
                            'field_id': 'node_projection',
                            'label': 'Node projection', 'control': 'json',
                            'required': True,
                        }, {
                            'field_id': 'relationship_projection',
                            'label': 'Relationship projection',
                            'control': 'json', 'required': True,
                        }, {
                            'field_id': 'configuration',
                            'label': 'Projection configuration',
                            'control': 'json', 'json_type': 'object',
                            'required': False, 'default': {},
                        }],
                    }
        return catalog

    @staticmethod
    def _native_target(target):
        target = _mapping(target, 'target resource')
        native = target.get('native')
        if isinstance(native, Mapping):
            return copy.deepcopy(dict(native))
        extension = target.get('extensions', {}).get('neo4j', {})
        if isinstance(extension, Mapping) and isinstance(
            extension.get('native'), Mapping
        ):
            return copy.deepcopy(dict(extension['native']))
        raise Neo4jClientError('Neo4j target has no provider-native identity')

    def validate_admin_operation(self, request):
        kind = request['resource_kind']
        operation = request['operation_id']
        draft = request.get('draft', {})
        errors = []
        if draft.get('definition'):
            errors.append({
                'field_id': 'definition', 'code': 'raw_cypher_forbidden',
                'message': 'Use structured visual options; raw Cypher is not '
                           'accepted by administration forms.',
            })
        if kind in {'node', 'relationship', 'graph'} and operation == 'insert':
            values = draft.get('values')
            if not isinstance(values, Mapping):
                errors.append({
                    'field_id': 'values', 'code': 'object_required',
                    'message': 'Graph values must be an object.',
                })
            elif kind == 'relationship' and any(
                not isinstance(values.get(field), str) or not values[field]
                for field in (
                    'type', 'start_node_element_id', 'end_node_element_id'
                )
            ):
                errors.append({
                    'field_id': 'values', 'code': 'endpoints_required',
                    'message': 'Relationship type and both node endpoints '
                               'are required.',
                })
        if kind == 'alias' and operation == 'create':
            options = draft.get('options', {})
            if not isinstance(options, Mapping) or not isinstance(
                options.get('database'), str
            ):
                errors.append({
                    'field_id': 'options', 'code': 'database_required',
                    'message': 'Alias options require a database target.',
                })
            elif options.get('remote_url') and not options.get(
                'oidc_credential_forwarding'
            ) and not all(isinstance(options.get(field), str) for field in (
                'remote_username', 'credential_reference_id'
            )):
                errors.append({
                    'field_id': 'options',
                    'code': 'remote_credentials_required',
                    'message': 'A remote alias requires OIDC forwarding or '
                               'username and credential reference.',
                })
        if kind in {'index', 'constraint'} and operation == 'create':
            options = draft.get('options', {})
            if not isinstance(options, Mapping):
                errors.append({
                    'field_id': 'options', 'code': 'object_required',
                    'message': 'Schema options must be an object.',
                })
            elif options.get('type', 'range') != 'lookup' and not isinstance(
                options.get('properties'), list
            ):
                errors.append({
                    'field_id': 'options', 'code': 'properties_required',
                    'message': 'Schema options require a properties array.',
                })
        if kind == 'user' and operation in {'create', 'alter'}:
            options = draft.get('options', draft.get('changes', {}))
            if operation == 'create' and not isinstance(
                options.get('credential_reference_id')
                if isinstance(options, Mapping) else None, str
            ):
                errors.append({
                    'field_id': 'options', 'code': 'credential_required',
                    'message': (
                        'User creation requires a credential reference.'
                    ),
                })
        if kind == 'query-plan' and operation == 'execute':
            source = draft.get('source')
            if not isinstance(source, str) or not source.strip():
                errors.append({
                    'field_id': 'source', 'code': 'query_required',
                    'message': 'A Cypher query is required.',
                })
            elif len(source.encode('utf-8')) > MAX_QUERY_BYTES:
                errors.append({
                    'field_id': 'source', 'code': 'query_too_large',
                    'message': 'The Cypher query exceeds the safety limit.',
                })
            if not isinstance(draft.get('parameters', {}), Mapping):
                errors.append({
                    'field_id': 'parameters', 'code': 'object_required',
                    'message': 'Query parameters must be an object.',
                })
            if draft.get('mode', 'explain') not in {'explain', 'profile'}:
                errors.append({
                    'field_id': 'mode', 'code': 'mode_invalid',
                    'message': 'Plan mode must be explain or profile.',
                })
        if kind == 'procedure' and operation == 'execute' and not isinstance(
                draft.get('arguments', []), list):
            errors.append({
                'field_id': 'arguments', 'code': 'array_required',
                'message': 'Procedure arguments must be a JSON array.',
            })
        if kind == 'graph-projection' and operation == 'create':
            admitted_types = (str, list, Mapping)
            for field_id in (
                    'node_projection', 'relationship_projection'):
                if not isinstance(draft.get(field_id), admitted_types):
                    errors.append({
                        'field_id': field_id,
                        'code': 'projection_required',
                        'message': (
                            'Projection must be a label/type string, array '
                            'or object.'
                        ),
                    })
            if not isinstance(draft.get('configuration', {}), Mapping):
                errors.append({
                    'field_id': 'configuration',
                    'code': 'object_required',
                    'message': 'Projection configuration must be an object.',
                })
        return {'errors': errors}

    def plan_admin_operation(self, request):
        native = self._native_target(request['target_resource']) \
            if request.get('target_resource') else {}
        draft = copy.deepcopy(request.get('draft', {}))
        preview = copy.deepcopy(draft)
        for container in ('options', 'changes'):
            if isinstance(preview.get(container), Mapping) and \
                    'credential_reference_id' in preview[container]:
                preview[container]['credential_reference_id'] = \
                    '<secret-reference>'
        payload = {
            'resource_kind': request['resource_kind'],
            'operation_id': request['operation_id'],
            'draft': draft, 'native': native,
            '_provider_route': copy.deepcopy(request.get('_provider_route')),
        }
        return {
            'command_preview': {
                'driver': 'neo4j-python-driver',
                'language': 'Cypher',
                'resource_kind': request['resource_kind'],
                'operation': request['operation_id'],
                'target': native, 'arguments': preview,
                'values_parameterized': True,
            },
            'provider_payload': payload, 'warnings': [],
            'receipt': {
                'provider_owned': True,
                'common_transaction_finality_inference': False,
            },
        }

    def apply_admin_operation(self, request):
        payload = _mapping(request.get('provider_payload'), 'provider plan')
        route = payload.pop('_provider_route', None)
        if payload['resource_kind'] in {
            'backup', 'restore', 'import', 'export', 'shell',
            'consistency-check',
        }:
            return self._apply_tool(payload, self._route({'route': route}))
        driver, route = self._connect({'route': route})
        session = None
        try:
            database = 'system' if payload['resource_kind'] in {
                'dbms', 'server', 'database', 'composite-database', 'alias',
                'user', 'role', 'privilege',
            } else route.get('database')
            session = self._session(driver, route, database)
            if payload['resource_kind'] == 'graph-projection':
                version_result = session.run(
                    'RETURN gds.version() AS cdeadmin_gds_version'
                )
                version_record = version_result.single()
                version_result.consume()
                version = (
                    _record_data(version_record).get('cdeadmin_gds_version')
                    if version_record is not None else None
                )
                if version != QUALIFIED_GDS_VERSION:
                    raise Neo4jClientError(
                        'the qualified Neo4j GDS 2026.04.0 surface is '
                        'unavailable on this endpoint'
                    )
            statement, parameters = self._admin_command(payload, route)
            result = session.run(statement, parameters)
            records = [
                self._json_value(_record_data(row)) for row in result
            ][:MAX_PAGE_SIZE]
            summary = self._summary(result.consume())
            return {
                'records': records, 'summary': summary,
                'driver_observation_only': True,
                'transaction_finality_interpreted_by_common_code': False,
            }
        except Neo4jClientError:
            raise
        except Exception as exc:
            raise Neo4jClientError(
                f'Neo4j visual administration failed ({type(exc).__name__})'
            ) from None
        finally:
            if session is not None:
                session.close()
            self._forget_driver(driver)

    def _admin_command(self, payload, route):
        kind = payload['resource_kind']
        operation = payload['operation_id']
        draft = payload['draft']
        native = payload['native']
        if operation == 'inspect':
            return self._inspect_command(kind, native)
        if kind in {'node', 'relationship', 'graph'}:
            return self._graph_command(kind, operation, draft, native)
        if kind in {'database', 'composite-database'}:
            return self._database_command(kind, operation, draft, native)
        if kind == 'alias':
            return self._alias_command(
                operation, draft, native, route
            )
        if kind in {'index', 'constraint'}:
            return self._schema_command(kind, operation, draft, native)
        if kind == 'graph-projection':
            return self._graph_projection_command(
                operation, draft, native
            )
        if kind in {'user', 'role', 'privilege'}:
            return self._security_command(
                kind, operation, draft, native, route
            )
        if kind in {
                'dbms', 'server', 'transaction', 'query', 'query-plan',
                'procedure'}:
            return self._operational_command(kind, operation, draft, native)
        raise Neo4jClientError(
            'Neo4j administration operation is unavailable'
        )

    @staticmethod
    def _workspace_path(workspace, value, label):
        root = Path(_identifier(workspace, 'tool workspace')).resolve(
            strict=False
        )
        if not root.is_absolute() or root in {Path('/'), Path.home()}:
            raise Neo4jClientError('tool workspace grant is unsafe')
        candidate = Path(_identifier(value, label))
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError:
            raise Neo4jClientError(
                f'{label} escapes the tool workspace'
            ) from None
        if candidate == root:
            raise Neo4jClientError(f'{label} must identify a child path')
        return root, candidate

    def _tool_grant(self, executable, route):
        return ProviderToolGrant(
            executable,
            _identifier(route.get('tool_workspace'), 'tool workspace'),
            _identifier(route.get('host'), 'endpoint host'),
            _bounded_int(
                route.get('port'), 7687, 1, 65535, 'endpoint port'
            ),
            secret_environment_names=('NEO4J_PASSWORD',),
        )

    def _tool_secret(self, route, callback):
        reference = route.get('credential_reference_id')
        if reference is None:
            return callback(None)
        if not callable(self._secret_acquirer):
            raise Neo4jClientError('Neo4j credential binding is unavailable')
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
        kind = payload['resource_kind']
        draft = payload['draft']
        if payload['operation_id'] == 'inspect':
            return {
                'available_executables': self._tool_runner.available(),
                'driver_observation_only': True,
                'transaction_finality_interpreted_by_common_code': False,
            }
        action = _identifier(draft.get('action'), 'tool action')
        arguments = _mapping(draft.get('arguments', {}), 'tool arguments')
        workspace, path = self._workspace_path(
            route.get('tool_workspace'),
            arguments.get('path', '.cdeadmin-neo4j-observation'),
            'tool path',
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        database = _identifier(
            arguments.get('database', route.get('database') or 'neo4j'),
            'database',
        )
        executable = {
            'backup': 'neo4j-admin-backup',
            'restore': 'neo4j-admin-restore',
            'import': 'neo4j-admin-import',
            'export': 'neo4j-admin-dump',
            'shell': 'cypher-shell',
            'consistency-check': 'neo4j-admin-check',
        }[kind]
        grant = self._tool_grant(executable, route)
        cli = []
        secret_environment = None
        secret_config = None
        secret_argument = '--file={path}'
        if kind == 'backup' and action == 'backup':
            cli = ['database', 'backup', database, f'--to-path={path}']
        elif kind == 'restore' and action in {'load', 'restore'}:
            if not path.exists():
                raise Neo4jClientError('restore source is unavailable')
            subcommand = 'load' if action == 'load' else 'restore'
            cli = [
                'database', subcommand, database, f'--from-path={path}'
            ]
            if arguments.get('overwrite_destination'):
                cli.append('--overwrite-destination=true')
        elif kind == 'export' and action == 'dump':
            cli = ['database', 'dump', database, f'--to-path={path}']
        elif kind == 'import' and action in {'full', 'incremental'}:
            nodes = arguments.get('nodes')
            relationships = arguments.get('relationships')
            cli = ['database', 'import', action, database]
            if nodes:
                _root, node_path = self._workspace_path(
                    workspace, nodes, 'nodes path'
                )
                if not node_path.exists():
                    raise Neo4jClientError(
                        'nodes import source is unavailable'
                    )
                cli.append(f'--nodes={node_path}')
            if relationships:
                _root, rel_path = self._workspace_path(
                    workspace, relationships, 'relationships path'
                )
                if not rel_path.exists():
                    raise Neo4jClientError(
                        'relationships import source is unavailable'
                    )
                cli.append(f'--relationships={rel_path}')
        elif kind == 'consistency-check' and action == 'check':
            cli = ['database', 'check', database, f'--report-path={path}']
        elif kind == 'shell' and action == 'script':
            script = _identifier(arguments.get('script'), 'Cypher script')
            cli = [
                f'--address={self._uri(route)}',
                f'--database={database}', '--format=plain',
            ]
            if route.get('username'):
                cli.append('--username=' + _identifier(
                    route['username'], 'Neo4j username'
                ))
            secret_config = script.encode('utf-8')
            secret_argument = '--file={path}'
        else:
            raise Neo4jClientError('Neo4j tool action is unavailable')

        def invoke(password):
            environment = None
            if password is not None:
                environment = {'NEO4J_PASSWORD': password}
            return self._tool_runner.run(
                grant, cli, secret_config=secret_config,
                secret_argument=secret_argument, secret_suffix='.cypher',
                redact_values=(str(password or ''),),
                secret_environment=environment,
            )

        try:
            observation = self._tool_secret(route, invoke)
        except ProviderToolError as exc:
            raise Neo4jClientError(str(exc)) from None
        if observation['return_code'] != 0:
            raise Neo4jClientError(
                f'Neo4j {executable} exited with a failure: '
                f'{observation["stderr"][-500:]}'
            )
        observation.update({
            'operation': action, 'acknowledged': True,
            'driver_observation_only': True,
            'transaction_finality_interpreted_by_common_code': False,
        })
        return observation

    @staticmethod
    def _inspect_command(kind, native):
        statements = {
            'dbms': 'CALL dbms.components() YIELD * RETURN *',
            'server': 'SHOW SERVERS YIELD * RETURN *',
            'database': 'SHOW DATABASES YIELD * RETURN *',
            'composite-database': 'SHOW DATABASES YIELD * RETURN *',
            'alias': 'SHOW ALIASES FOR DATABASES YIELD * RETURN *',
            'label': 'CALL db.labels() YIELD label RETURN label',
            'relationship-type': (
                'CALL db.relationshipTypes() YIELD * RETURN *'
            ),
            'property': 'CALL db.propertyKeys() YIELD * RETURN *',
            'index': 'SHOW INDEXES YIELD * RETURN *',
            'constraint': 'SHOW CONSTRAINTS YIELD * RETURN *',
            'procedure': 'SHOW PROCEDURES YIELD * RETURN *',
            'function': 'SHOW FUNCTIONS YIELD * RETURN *',
            'setting': 'SHOW SETTINGS YIELD * RETURN *',
            'transaction': 'SHOW TRANSACTIONS YIELD * RETURN *',
            'query': 'SHOW TRANSACTIONS YIELD * RETURN *',
            'query-plan': 'EXPLAIN RETURN 1 AS cdeadmin_plan_probe',
            'graph-projection': 'CALL gds.graph.list($graph_name)',
            'user': 'SHOW USERS YIELD * RETURN *',
            'role': 'SHOW ROLES YIELD * RETURN *',
            'privilege': 'SHOW PRIVILEGES YIELD * RETURN *',
            'graph': (
                'MATCH (n) OPTIONAL MATCH (n)-[r]->() '
                'RETURN n, r LIMIT 200'
            ),
            'node': 'MATCH (n) WHERE elementId(n) = $id RETURN n',
            'relationship': (
                'MATCH ()-[r]->() WHERE elementId(r) = $id RETURN r'
            ),
        }
        if kind not in statements:
            raise Neo4jClientError('Neo4j inspect operation is unavailable')
        parameters = {'id': native.get('element_id')}
        if kind == 'graph-projection':
            parameters = {'graph_name': _identifier(
                native.get('graphName') or native.get('name'),
                'GDS graph name',
            )}
        return statements[kind], parameters

    @staticmethod
    def _properties(value, label):
        result = _mapping(value or {}, label)
        if len(result) > MAX_PROPERTIES:
            raise Neo4jClientError(f'{label} exceeds the property limit')
        return result

    def _graph_command(self, kind, operation, draft, native):
        values = draft.get('values', {})
        if kind == 'graph' and operation == 'insert':
            kind = str(values.get('kind', 'node'))
        if kind == 'node':
            if operation == 'insert':
                labels = values.get('labels', [])
                if not isinstance(labels, list):
                    raise Neo4jClientError('node labels must be an array')
                label_source = ''.join(
                    ':' + _quoted(item, 'node label') for item in labels
                )
                return (
                    f'CREATE (n{label_source}) SET n += $properties RETURN n',
                    {'properties': self._properties(
                        values.get('properties', {}), 'node properties'
                    )},
                )
            element_id = native.get('element_id') or draft.get(
                'selector', {}
            ).get('element_id')
            _identifier(element_id, 'node element ID')
            if operation == 'update':
                changes = draft.get('changes', {})
                properties_source = changes.get('properties')
                if properties_source is None:
                    properties_source = {
                        key: value for key, value in changes.items()
                        if key not in {'add_labels', 'remove_labels'}
                    }
                properties = self._properties(
                    properties_source, 'node changes'
                )
                clauses = ['SET n += $properties']
                add_labels = changes.get('add_labels', [])
                remove_labels = changes.get('remove_labels', [])
                if not isinstance(add_labels, list) or not isinstance(
                    remove_labels, list
                ):
                    raise Neo4jClientError(
                        'node label changes must be arrays'
                    )
                if add_labels:
                    clauses.append('SET n' + ''.join(
                        ':' + _quoted(item, 'node label')
                        for item in add_labels
                    ))
                if remove_labels:
                    clauses.append('REMOVE n' + ''.join(
                        ':' + _quoted(item, 'node label')
                        for item in remove_labels
                    ))
                return (
                    'MATCH (n) WHERE elementId(n) = $id '
                    + ' '.join(clauses) + ' RETURN n',
                    {'id': element_id, 'properties': properties},
                )
            if operation == 'delete':
                selector = draft.get('selector', {})
                detach = selector.get('detach', True) if isinstance(
                    selector, Mapping
                ) else True
                verb = 'DETACH DELETE' if detach else 'DELETE'
                return (
                    f'MATCH (n) WHERE elementId(n) = $id {verb} n '
                    'RETURN $id AS deletedElementId', {'id': element_id},
                )
        if kind == 'relationship':
            if operation == 'insert':
                rel_type = _quoted(values.get('type'), 'relationship type')
                start = _identifier(
                    values.get('start_node_element_id'), 'start node ID'
                )
                end = _identifier(
                    values.get('end_node_element_id'), 'end node ID'
                )
                return (
                    'MATCH (a), (b) WHERE elementId(a) = $start AND '
                    f'elementId(b) = $end CREATE (a)-[r:{rel_type}]->(b) '
                    'SET r += $properties RETURN r',
                    {
                        'start': start, 'end': end,
                        'properties': self._properties(
                            values.get('properties', {}),
                            'relationship properties',
                        ),
                    },
                )
            element_id = native.get('element_id') or draft.get(
                'selector', {}
            ).get('element_id')
            _identifier(element_id, 'relationship element ID')
            if operation == 'update':
                changes = draft.get('changes', {})
                properties_source = changes.get('properties')
                if properties_source is None:
                    properties_source = changes
                return (
                    'MATCH ()-[r]->() WHERE elementId(r) = $id '
                    'SET r += $properties RETURN r',
                    {'id': element_id, 'properties': self._properties(
                        properties_source,
                        'relationship changes',
                    )},
                )
            if operation == 'delete':
                return (
                    'MATCH ()-[r]->() WHERE elementId(r) = $id DELETE r '
                    'RETURN $id AS deletedElementId', {'id': element_id},
                )
        raise Neo4jClientError('graph data operation is unavailable')

    @staticmethod
    def _graph_projection_command(operation, draft, native):
        if operation == 'create':
            name = _identifier(draft.get('name'), 'GDS graph name')
            node_projection = draft.get('node_projection')
            relationship_projection = draft.get(
                'relationship_projection'
            )
            admitted = (str, list, Mapping)
            if not isinstance(node_projection, admitted) or not isinstance(
                    relationship_projection, admitted):
                raise Neo4jClientError(
                    'GDS projections must be strings, arrays or objects'
                )
            configuration = _mapping(
                draft.get('configuration', {}),
                'GDS projection configuration',
            )
            return (
                'CALL gds.graph.project($graph_name, $node_projection, '
                '$relationship_projection, $configuration)',
                {
                    'graph_name': name,
                    'node_projection': copy.deepcopy(node_projection),
                    'relationship_projection': copy.deepcopy(
                        relationship_projection
                    ),
                    'configuration': configuration,
                },
            )
        if operation == 'drop':
            name = _identifier(
                native.get('graphName') or native.get('name'),
                'GDS graph name',
            )
            return (
                'CALL gds.graph.drop($graph_name, $fail_if_missing)',
                {'graph_name': name, 'fail_if_missing': False},
            )
        raise Neo4jClientError(
            'GDS graph projection operation is unavailable'
        )

    @staticmethod
    def _database_command(kind, operation, draft, native):
        current = native.get('name')
        if operation == 'create':
            name = _quoted(draft.get('name'), 'database name')
            composite = ' COMPOSITE' if kind == 'composite-database' else ''
            options = _mapping(
                draft.get('options', {}), 'database options'
            )
            clauses = []
            parameters = {}
            default_language = options.get('default_language')
            if default_language is not None:
                if kind == 'composite-database':
                    raise Neo4jClientError(
                        'composite databases have no default language option'
                    )
                if default_language not in {'CYPHER 5', 'CYPHER 25'}:
                    raise Neo4jClientError(
                        'database default language is unavailable'
                    )
                clauses.append(f'DEFAULT LANGUAGE {default_language}')
            topology = options.get('topology')
            if topology is not None:
                if kind == 'composite-database':
                    raise Neo4jClientError(
                        'composite databases have no topology option'
                    )
                topology = _mapping(topology, 'database topology')
                pieces = []
                for field, word in (
                    ('primaries', 'PRIMARY'),
                    ('secondaries', 'SECONDARY'),
                ):
                    if topology.get(field) is not None:
                        parameters[field] = _bounded_int(
                            topology[field], 1, 0, 1000, field
                        )
                        pieces.append(f'${field} {word}')
                if not pieces:
                    raise Neo4jClientError('database topology is empty')
                clauses.append('TOPOLOGY ' + ' '.join(pieces))
            graph_shard = options.get('graph_shard')
            if graph_shard is not None:
                if kind == 'composite-database':
                    raise Neo4jClientError(
                        'composite databases have no graph shard option'
                    )
                graph_shard = _mapping(graph_shard, 'graph shard')
                pieces = []
                for field, word in (
                    ('primaries', 'PRIMARY'),
                    ('secondaries', 'SECONDARY'),
                ):
                    if graph_shard.get(field) is not None:
                        parameter = f'graph_shard_{field}'
                        parameters[parameter] = _bounded_int(
                            graph_shard[field], 1, 0, 1000, parameter
                        )
                        pieces.append(f'${parameter} {word}')
                if not pieces:
                    raise Neo4jClientError('graph shard topology is empty')
                clauses.append(
                    'SET GRAPH SHARD { TOPOLOGY ' + ' '.join(pieces) + ' }'
                )
            property_shards = options.get('property_shards')
            if property_shards is not None:
                if kind == 'composite-database':
                    raise Neo4jClientError(
                        'composite databases have no property shard option'
                    )
                property_shards = _mapping(
                    property_shards, 'property shards'
                )
                parameters['property_shard_count'] = _bounded_int(
                    property_shards.get('count'), 1, 1, 1000,
                    'property shard count',
                )
                source = 'SET PROPERTY SHARDS { COUNT $property_shard_count'
                if property_shards.get('replicas') is not None:
                    parameters['property_shard_replicas'] = _bounded_int(
                        property_shards['replicas'], 1, 0, 1000,
                        'property shard replicas',
                    )
                    source += (
                        ' TOPOLOGY $property_shard_replicas REPLICA'
                    )
                clauses.append(source + ' }')
            native_options = options.get('options')
            if native_options is not None:
                parameters['database_options'] = _mapping(
                    native_options, 'database native options'
                )
                clauses.append('OPTIONS $database_options')
            wait = options.get('wait_seconds')
            if wait is not None:
                parameters['wait_seconds'] = _bounded_int(
                    wait, 0, 0, 3600, 'wait seconds'
                )
                clauses.append('WAIT $wait_seconds SECONDS')
            elif options.get('wait') is False:
                clauses.append('NOWAIT')
            suffix = (' ' + ' '.join(clauses)) if clauses else ''
            return (
                f'CREATE{composite} DATABASE {name} IF NOT EXISTS{suffix}',
                parameters,
            )
        name = _quoted(current, 'database name')
        if operation == 'drop':
            composite = 'COMPOSITE ' if kind == 'composite-database' else ''
            disposition = draft.get('data_disposition', 'destroy')
            aliases = draft.get('alias_action', 'restrict')
            if disposition not in {'dump', 'destroy'}:
                raise Neo4jClientError('database data disposition is invalid')
            if aliases not in {'restrict', 'cascade'}:
                raise Neo4jClientError('database alias action is invalid')
            alias_clause = (
                'CASCADE ALIASES' if aliases == 'cascade' else 'RESTRICT'
            )
            parameters = {}
            wait_clause = ''
            if draft.get('wait_seconds') is not None:
                parameters['wait_seconds'] = _bounded_int(
                    draft['wait_seconds'], 0, 0, 3600, 'wait seconds'
                )
                wait_clause = ' WAIT $wait_seconds SECONDS'
            return (
                f'DROP {composite}DATABASE {name} IF EXISTS {alias_clause} '
                f'{disposition.upper()} DATA{wait_clause}', parameters,
            )
        if operation == 'alter':
            changes = _mapping(draft.get('changes'), 'database changes')
            action = changes.get('action')
            if action in {'start', 'stop'}:
                return f'{action.upper()} DATABASE {name}', {}
            clauses = []
            parameters = {}
            access = changes.get('access')
            if access is not None:
                admitted = {
                    'read-only': 'READ ONLY', 'read-write': 'READ WRITE',
                }
                if access not in admitted:
                    raise Neo4jClientError('database access mode is invalid')
                clauses.append('SET ACCESS ' + admitted[access])
            topology = changes.get('topology')
            if topology is not None:
                topology = _mapping(topology, 'database topology')
                pieces = []
                for field, word in (
                    ('primaries', 'PRIMARY'),
                    ('secondaries', 'SECONDARY'),
                ):
                    if topology.get(field) is not None:
                        parameters[field] = _bounded_int(
                            topology[field], 1, 0, 1000, field
                        )
                        pieces.append(f'${field} {word}')
                if pieces:
                    clauses.append('SET TOPOLOGY ' + ' '.join(pieces))
            default_language = changes.get('default_language')
            if default_language is not None:
                if default_language not in {'CYPHER 5', 'CYPHER 25'}:
                    raise Neo4jClientError(
                        'database default language is unavailable'
                    )
                clauses.append(
                    'SET DEFAULT LANGUAGE ' + default_language
                )
            graph_shard = changes.get('graph_shard')
            if graph_shard is not None:
                graph_shard = _mapping(graph_shard, 'graph shard')
                pieces = []
                for field, word in (
                    ('primaries', 'PRIMARY'),
                    ('secondaries', 'SECONDARY'),
                ):
                    if graph_shard.get(field) is not None:
                        parameter = f'graph_shard_{field}'
                        parameters[parameter] = _bounded_int(
                            graph_shard[field], 1, 0, 1000, parameter
                        )
                        pieces.append(f'${parameter} {word}')
                if pieces:
                    clauses.append(
                        'SET GRAPH SHARD { SET TOPOLOGY ' +
                        ' '.join(pieces) + ' }'
                    )
            property_shard = changes.get('property_shard')
            if property_shard is not None:
                property_shard = _mapping(
                    property_shard, 'property shard'
                )
                replicas = _bounded_int(
                    property_shard.get('replicas'), 1, 0, 1000,
                    'property shard replicas',
                )
                parameters['property_shard_replicas'] = replicas
                clauses.append(
                    'SET PROPERTY SHARD { SET TOPOLOGY '
                    '$property_shard_replicas REPLICA }'
                )
            set_options = changes.get('set_options', {})
            if not isinstance(set_options, Mapping):
                raise Neo4jClientError(
                    'database set options must be an object'
                )
            for index, (key, value) in enumerate(set_options.items()):
                parameter = f'option_{index}'
                clauses.append(
                    f'SET OPTION {_quoted(key, "database option")} '
                    f'${parameter}'
                )
                parameters[parameter] = value
            remove_options = changes.get('remove_options', [])
            if not isinstance(remove_options, list):
                raise Neo4jClientError(
                    'database remove options must be an array'
                )
            for key in remove_options:
                clauses.append(
                    'REMOVE OPTION ' + _quoted(key, 'database option')
                )
            if not clauses:
                raise Neo4jClientError('database changes are empty')
            return f'ALTER DATABASE {name} ' + ' '.join(clauses), parameters
        raise Neo4jClientError('database operation is unavailable')

    def _alias_command(self, operation, draft, native, route):
        if operation == 'create':
            options = _mapping(draft.get('options', {}), 'alias options')
            statement = (
                f'CREATE ALIAS {_quoted(draft.get("name"), "alias name")} '
                'IF NOT EXISTS FOR DATABASE $target'
            )
            parameters = {
                'target': _identifier(options.get('database'), 'database')
            }
            remote_url = options.get('remote_url')
            if remote_url is not None:
                remote_url = _identifier(remote_url, 'remote alias URL')
                parsed = urlsplit(remote_url)
                if parsed.scheme not in {
                    'neo4j', 'neo4j+s', 'neo4j+ssc', 'bolt', 'bolt+s',
                    'bolt+ssc',
                } or not parsed.hostname or parsed.username is not None:
                    raise Neo4jClientError('remote alias URL is invalid')
                parameters['url'] = remote_url
                statement += ' AT $url'
                if options.get('oidc_credential_forwarding'):
                    statement += ' OIDC CREDENTIAL FORWARDING'
                else:
                    parameters['remote_user'] = _identifier(
                        options.get('remote_username'), 'remote username'
                    )
                    reference = _identifier(
                        options.get('credential_reference_id'),
                        'credential reference',
                    )
                    if not callable(self._secret_acquirer):
                        raise Neo4jClientError(
                            'remote alias credential binding unavailable'
                        )
                    lease = self._secret_acquirer(
                        reference,
                        _identifier(
                            route.get('principal_reference'),
                            'principal reference',
                        ),
                        'administer', 'database_password',
                    )
                    with lease:
                        parameters['remote_password'] = lease.use(
                            lambda value: bytes(value).decode('utf-8')
                        )
                    statement += ' USER $remote_user PASSWORD $remote_password'
                if options.get('driver') is not None:
                    parameters['driver'] = _mapping(
                        options['driver'], 'remote alias driver options'
                    )
                    statement += ' DRIVER $driver'
            if options.get('properties') is not None:
                parameters['properties'] = _mapping(
                    options['properties'], 'alias properties'
                )
                statement += ' PROPERTIES $properties'
            return statement, parameters
        name = _quoted(native.get('name'), 'alias name')
        if operation == 'drop':
            return f'DROP ALIAS {name} IF EXISTS FOR DATABASE', {}
        if operation == 'alter':
            changes = _mapping(draft.get('changes'), 'alias changes')
            clauses = []
            parameters = {}
            if changes.get('database') is not None:
                clauses.append('TARGET $target')
                parameters['target'] = _identifier(
                    changes['database'], 'database'
                )
            if changes.get('remote_url') is not None:
                remote_url = _identifier(
                    changes['remote_url'], 'remote alias URL'
                )
                parsed = urlsplit(remote_url)
                if parsed.scheme not in {
                    'neo4j', 'neo4j+s', 'neo4j+ssc', 'bolt', 'bolt+s',
                    'bolt+ssc',
                } or not parsed.hostname or parsed.username is not None:
                    raise Neo4jClientError('remote alias URL is invalid')
                clauses.append('AT $url')
                parameters['url'] = remote_url
            if changes.get('remote_username') is not None:
                clauses.append('USER $remote_user')
                parameters['remote_user'] = _identifier(
                    changes['remote_username'], 'remote username'
                )
            if changes.get('credential_reference_id') is not None:
                if not callable(self._secret_acquirer):
                    raise Neo4jClientError(
                        'remote alias credential binding unavailable'
                    )
                lease = self._secret_acquirer(
                    _identifier(
                        changes['credential_reference_id'],
                        'credential reference',
                    ),
                    _identifier(
                        route.get('principal_reference'),
                        'principal reference',
                    ),
                    'administer', 'database_password',
                )
                with lease:
                    parameters['remote_password'] = lease.use(
                        lambda value: bytes(value).decode('utf-8')
                    )
                clauses.append('PASSWORD $remote_password')
            if changes.get('driver') is not None:
                clauses.append('DRIVER $driver')
                parameters['driver'] = _mapping(
                    changes['driver'], 'remote alias driver options'
                )
            if changes.get('properties') is not None:
                clauses.append('PROPERTIES $properties')
                parameters['properties'] = _mapping(
                    changes['properties'], 'alias properties'
                )
            if not clauses:
                raise Neo4jClientError('alias changes are empty')
            return (
                f'ALTER ALIAS {name} IF EXISTS SET DATABASE ' +
                ' '.join(clauses), parameters,
            )
        raise Neo4jClientError('alias operation is unavailable')

    @staticmethod
    def _schema_command(kind, operation, draft, native):
        if operation == 'drop':
            name = _quoted(native.get('name'), f'{kind} name')
            return f'DROP {kind.upper()} {name} IF EXISTS', {}
        options = _mapping(draft.get('options', {}), f'{kind} options')
        name = _quoted(draft.get('name'), f'{kind} name')
        entity_type = options.get('entity_type', 'node')
        if entity_type not in {'node', 'relationship'}:
            raise Neo4jClientError('schema entity type is invalid')
        properties = options.get('properties')
        if not isinstance(properties, list):
            raise Neo4jClientError(
                'schema properties must be an array'
            )
        variable = 'n' if entity_type == 'node' else 'r'
        expressions = ', '.join(
            f'{variable}.{_quoted(item, "property")}' for item in properties
        )
        token = options.get(
            'label' if entity_type == 'node' else 'relationship_type'
        )
        quoted_token = _quoted(
            token, 'label' if entity_type == 'node' else 'relationship type'
        ) if token is not None else None
        if kind == 'index':
            index_type = str(options.get('type', 'range')).upper()
            if index_type not in {
                'RANGE', 'TEXT', 'POINT', 'VECTOR', 'FULLTEXT', 'LOOKUP',
            }:
                raise Neo4jClientError('index type is unavailable')
            pattern = (
                f'(n:{quoted_token})' if entity_type == 'node' else
                f'()-[r:{quoted_token}]-()'
            )
            parameters = {}
            native_options = options.get('options')
            suffix = ''
            if native_options is not None:
                parameters['index_options'] = _mapping(
                    native_options, 'index native options'
                )
                suffix = ' OPTIONS $index_options'
            if index_type == 'LOOKUP':
                lookup_pattern = (
                    '(n)' if entity_type == 'node' else '()-[r]-()'
                )
                function = 'labels(n)' if entity_type == 'node' else 'type(r)'
                return (
                    f'CREATE LOOKUP INDEX {name} IF NOT EXISTS FOR '
                    f'{lookup_pattern} ON EACH {function}{suffix}',
                    parameters,
                )
            if not properties:
                raise Neo4jClientError(
                    'index properties must be a non-empty array'
                )
            if index_type == 'FULLTEXT':
                labels = options.get('tokens', [token])
                if not isinstance(labels, list) or not labels:
                    raise Neo4jClientError(
                        'full-text index tokens must be a non-empty array'
                    )
                separator = '|' if entity_type == 'node' else '|'
                token_source = separator.join(
                    _quoted(item, 'full-text token') for item in labels
                )
                pattern = (
                    f'(n:{token_source})' if entity_type == 'node' else
                    f'()-[r:{token_source}]-()'
                )
                return (
                    f'CREATE FULLTEXT INDEX {name} IF NOT EXISTS FOR '
                    f'{pattern} ON EACH [{expressions}]{suffix}', parameters,
                )
            return (
                f'CREATE {index_type} INDEX {name} IF NOT EXISTS '
                f'FOR {pattern} ON ({expressions}){suffix}', parameters,
            )
        if not properties or quoted_token is None:
            raise Neo4jClientError(
                'constraint token and properties are required'
            )
        constraint_type = str(options.get('type', 'unique')).lower()
        requirement = {
            'unique': 'IS UNIQUE',
            'node-key': 'IS NODE KEY',
            'relationship-key': 'IS RELATIONSHIP KEY',
            'exists': 'IS NOT NULL',
        }.get(constraint_type)
        if constraint_type == 'type':
            property_type = _identifier(
                options.get('property_type'), 'property type'
            ).upper()
            if not all(
                char.isalnum() or char in {' ', '|', '<', '>', '[', ']'}
                for char in property_type
            ):
                raise Neo4jClientError('property type is invalid')
            requirement = f'IS :: {property_type}'
        if requirement is None:
            raise Neo4jClientError('constraint type is unavailable')
        subject = f'({expressions})' if len(properties) > 1 else expressions
        pattern = (
            f'(n:{quoted_token})' if entity_type == 'node' else
            f'()-[r:{quoted_token}]-()'
        )
        return (
            f'CREATE CONSTRAINT {name} IF NOT EXISTS FOR {pattern} '
            f'REQUIRE {subject} {requirement}', {},
        )

    def _security_command(self, kind, operation, draft, native, route):
        name = draft.get('name') if operation == 'create' else (
            native.get('user') or native.get('role') or native.get('name')
        )
        quoted = _quoted(name, f'{kind} name') if name else None
        if kind == 'user':
            if operation in {'create', 'alter'}:
                options = draft.get('options', draft.get('changes', {}))
                options = _mapping(options, 'user options')
                parameters = {}
                clauses = []
                reference = options.pop('credential_reference_id', None)
                if reference is not None:
                    principal = route.get('principal_reference')
                    if not callable(self._secret_acquirer):
                        raise Neo4jClientError(
                            'user credential binding unavailable'
                        )
                    lease = self._secret_acquirer(
                        _identifier(reference, 'credential reference'),
                        _identifier(principal, 'principal reference'),
                        'administer', 'database_password',
                    )
                    with lease:
                        parameters['password'] = lease.use(
                            lambda value: bytes(value).decode()
                        )
                    clauses.append('SET PASSWORD $password')
                    required = options.get(
                        'password_change_required', operation == 'create'
                    )
                    clauses.append(
                        'CHANGE REQUIRED' if required else
                        'CHANGE NOT REQUIRED'
                    )
                status = options.get('status')
                if status is not None:
                    if status not in {'active', 'suspended'}:
                        raise Neo4jClientError('user status is invalid')
                    clauses.append('SET STATUS ' + status.upper())
                if options.get('remove_home_database'):
                    clauses.append('REMOVE HOME DATABASE')
                elif options.get('home_database') is not None:
                    clauses.append(
                        'SET HOME DATABASE ' + _quoted(
                            options['home_database'], 'home database'
                        )
                    )
                if operation == 'create':
                    if reference is None:
                        raise Neo4jClientError(
                            'user credential binding unavailable'
                        )
                    return (
                        f'CREATE USER {quoted} IF NOT EXISTS ' +
                        ' '.join(clauses), parameters,
                    )
                if not clauses:
                    raise Neo4jClientError('user changes are empty')
                return (
                    f'ALTER USER {quoted} IF EXISTS ' + ' '.join(clauses),
                    parameters,
                )
            if operation == 'rename':
                return (
                    f'RENAME USER {quoted} TO '
                    f'{_quoted(draft.get("new_name"), "new user name")}', {},
                )
            if operation == 'drop':
                return f'DROP USER {quoted} IF EXISTS', {}
        if kind == 'role':
            if operation == 'create':
                options = _mapping(
                    draft.get('options', {}), 'role options'
                )
                source = ''
                if options.get('copy_of') is not None:
                    source = ' AS COPY OF ' + _quoted(
                        options['copy_of'], 'source role'
                    )
                return f'CREATE ROLE {quoted} IF NOT EXISTS{source}', {}
            if operation == 'rename':
                return (
                    f'RENAME ROLE {quoted} TO '
                    f'{_quoted(draft.get("new_name"), "new role name")}', {},
                )
            if operation == 'drop':
                return f'DROP ROLE {quoted} IF EXISTS', {}
            principal = _quoted(draft.get('principal'), 'principal')
            verb = 'GRANT' if operation == 'grant' else 'REVOKE'
            preposition = 'TO' if operation == 'grant' else 'FROM'
            return f'{verb} ROLE {quoted} {preposition} {principal}', {}
        if kind == 'privilege' and operation in {'grant', 'revoke'}:
            privileges = draft.get('privileges')
            if not isinstance(privileges, Mapping):
                raise Neo4jClientError(
                    'privilege descriptor must be an object'
                )
            action = ' '.join(
                str(privileges.get('action', '')).upper().split()
            )
            scope = str(privileges.get('scope', 'graph')).lower()
            admitted = {
                'graph': {
                    'ALL GRAPH PRIVILEGES', 'TRAVERSE', 'READ', 'MATCH',
                    'WRITE', 'CREATE ELEMENT', 'DELETE ELEMENT',
                    'SET PROPERTY', 'REMOVE PROPERTY', 'SET LABEL',
                    'REMOVE LABEL',
                },
                'database': {
                    'ALL DATABASE PRIVILEGES', 'ACCESS', 'START', 'STOP',
                    'CREATE INDEX', 'DROP INDEX', 'SHOW INDEX',
                    'INDEX MANAGEMENT', 'CREATE CONSTRAINT',
                    'DROP CONSTRAINT', 'SHOW CONSTRAINT',
                    'CONSTRAINT MANAGEMENT', 'CREATE NEW LABEL',
                    'CREATE NEW TYPE', 'CREATE NEW NAME', 'NAME MANAGEMENT',
                    'SHOW TRANSACTION', 'TERMINATE TRANSACTION',
                    'TRANSACTION MANAGEMENT',
                },
                'dbms': {
                    'ALL DBMS PRIVILEGES', 'ROLE MANAGEMENT',
                    'USER MANAGEMENT', 'DATABASE MANAGEMENT',
                    'COMPOSITE DATABASE MANAGEMENT', 'ALIAS MANAGEMENT',
                    'PRIVILEGE MANAGEMENT', 'SERVER MANAGEMENT',
                    'EXECUTE PROCEDURE', 'EXECUTE BOOSTED PROCEDURE',
                    'EXECUTE FUNCTION', 'EXECUTE BOOSTED FUNCTION',
                    'SHOW SETTING', 'IMPERSONATE',
                },
            }
            if scope not in admitted or action not in admitted[scope]:
                raise Neo4jClientError('privilege action is unavailable')
            if action in {'READ', 'MATCH', 'SET PROPERTY', 'REMOVE PROPERTY'}:
                properties = privileges.get('properties')
                if properties is not None:
                    if not isinstance(properties, list) or not properties:
                        raise Neo4jClientError(
                            'privilege properties must be a non-empty array'
                        )
                    action += ' {' + ', '.join(
                        _quoted(item, 'privilege property')
                        for item in properties
                    ) + '}'
            if scope == 'graph':
                graph = privileges.get('graph')
                if graph == '*':
                    graph_source = '*'
                elif graph in {'HOME', 'DEFAULT'}:
                    graph_source = graph + ' GRAPH'
                    scope_source = graph_source
                else:
                    graph_source = _quoted(graph, 'graph')
                if graph not in {'HOME', 'DEFAULT'}:
                    scope_source = f'GRAPH {graph_source}'
                resource = privileges.get('resource')
                if resource is not None:
                    resource = _mapping(resource, 'graph resource')
                    resource_kind = resource.get('kind', 'elements')
                    word = {
                        'nodes': 'NODES', 'relationships': 'RELATIONSHIPS',
                        'elements': 'ELEMENTS',
                    }.get(resource_kind)
                    if word is None:
                        raise Neo4jClientError(
                            'graph privilege resource is invalid'
                        )
                    names = resource.get('names', ['*'])
                    if names == ['*']:
                        target_source = '*'
                    elif isinstance(names, list) and names:
                        target_source = ', '.join(
                            _quoted(item, 'graph resource name')
                            for item in names
                        )
                    else:
                        raise Neo4jClientError(
                            'graph privilege resource names are invalid'
                        )
                    scope_source += f' {word} {target_source}'
            elif scope == 'database':
                database = privileges.get('database')
                if database == '*':
                    scope_source = 'DATABASE *'
                elif database in {'HOME', 'DEFAULT'}:
                    scope_source = database + ' DATABASE'
                else:
                    scope_source = 'DATABASE ' + _quoted(
                        database, 'database'
                    )
            else:
                scope_source = 'DBMS'
                pattern = privileges.get('executable_pattern')
                if pattern is not None:
                    pattern = _identifier(
                        pattern, 'executable pattern'
                    )
                    if not all(
                        char.isalnum() or char in {'_', '.', '*'}
                        for char in pattern
                    ):
                        raise Neo4jClientError(
                            'executable pattern is invalid'
                        )
                    action += ' ' + pattern
            role = _quoted(draft.get('principal'), 'role')
            effect = str(privileges.get('effect', 'grant')).lower()
            if effect not in {'grant', 'deny'}:
                raise Neo4jClientError('privilege effect is invalid')
            if operation == 'grant':
                verb = 'GRANT' if effect == 'grant' else 'DENY'
            else:
                verb = 'REVOKE GRANT' if effect == 'grant' else 'REVOKE DENY'
            preposition = 'TO' if operation == 'grant' else 'FROM'
            return (
                f'{verb} {action} ON {scope_source} '
                f'{preposition} {role}',
                {},
            )
        raise Neo4jClientError('security operation is unavailable')

    @staticmethod
    def _operational_command(kind, operation, draft, native):
        action = str(draft.get('action', '')).lower()
        if kind == 'query-plan' and operation == 'execute':
            source = draft.get('source')
            if not isinstance(source, str) or not source.strip():
                raise Neo4jClientError('Cypher query must not be empty')
            source = source.strip()
            if len(source.encode('utf-8')) > MAX_QUERY_BYTES:
                raise Neo4jClientError(
                    'Cypher query exceeds the safety limit'
                )
            mode = str(draft.get('mode', 'explain')).lower()
            if mode not in {'explain', 'profile'}:
                raise Neo4jClientError('query plan mode is invalid')
            parameters = _mapping(
                draft.get('parameters', {}), 'query plan parameters'
            )
            return f'{mode.upper()} {source}', parameters
        if kind == 'dbms' and action == 'clear-query-caches':
            return 'CALL db.clearQueryCaches() YIELD * RETURN *', {}
        if kind == 'server':
            server_id = _identifier(
                native.get('serverId') or native.get('name'), 'server ID'
            )
            if operation == 'alter':
                changes = _mapping(draft.get('changes'), 'server changes')
                return 'ALTER SERVER $server SET OPTIONS $options', {
                    'server': server_id,
                    'options': _mapping(
                        changes.get('options', changes), 'server options'
                    ),
                }
            if action == 'enable':
                arguments = _mapping(
                    draft.get('arguments', {}), 'server arguments'
                )
                return 'ENABLE SERVER $server OPTIONS $options', {
                    'server': server_id,
                    'options': _mapping(
                        arguments.get('options', {}), 'server options'
                    ),
                }
            if action == 'drop':
                return 'DROP SERVER $server', {'server': server_id}
            if action == 'deallocate-databases':
                return 'DEALLOCATE DATABASES FROM SERVER $server', {
                    'server': server_id
                }
        if kind == 'procedure' and action == 'execute':
            procedure = _identifier(native.get('name'), 'procedure name')
            source = '.'.join(
                _quoted(part, 'procedure name part')
                for part in procedure.split('.')
            )
            arguments = draft.get('arguments', [])
            if not isinstance(arguments, list):
                raise Neo4jClientError(
                    'procedure arguments must be an array'
                )
            if len(arguments) > MAX_PROPERTIES:
                raise Neo4jClientError(
                    'procedure argument count exceeds the safety limit'
                )
            parameters = {
                f'argument_{index}': value
                for index, value in enumerate(arguments)
            }
            parameter_source = ', '.join(
                f'$argument_{index}' for index in range(len(arguments))
            )
            return f'CALL {source}({parameter_source})', parameters
        if kind == 'transaction' and action == 'terminate':
            value = native.get('transactionId') or draft.get(
                'arguments', {}
            ).get('transaction_id')
            return 'TERMINATE TRANSACTION $transactionId', {
                'transactionId': _identifier(value, 'transaction ID')
            }
        if kind == 'query' and action == 'terminate':
            value = native.get('transactionId') or draft.get(
                'arguments', {}
            ).get('transaction_id')
            return 'TERMINATE TRANSACTION $transactionId', {
                'transactionId': _identifier(value, 'transaction ID')
            }
        if kind == 'database' and action in {'start', 'stop'}:
            return (
                f'{action.upper()} DATABASE {_quoted(native.get("name"))}',
                {},
            )
        raise Neo4jClientError('operational action is unavailable')

    def read_admin_rows(self, request):
        target = request['target_resource']
        native = self._native_target(target)
        limit = _bounded_int(
            request.get('limit'), 200, 1, MAX_PAGE_SIZE, 'graph page limit'
        )
        statement = (
            'MATCH (n) OPTIONAL MATCH (n)-[r]->(m) '
            'RETURN n, r, m LIMIT $limit'
        )
        records = self._read(
            request, statement, {'limit': limit}, native.get('database')
        )
        return {
            'schema': 'cdeadmin.visual-admin.graph-page.v1',
            'records': records, 'continuation': None,
            'identity_kind': 'neo4j-element-id',
            'editable': True, 'bounded': True,
        }

    @staticmethod
    def cancel_admin_cursor(_request):
        return {'cancelled': False, 'reason': 'graph pages are bounded'}

    def close(self):
        for result in list(self._results):
            if not result.complete:
                try:
                    self.cancel(result)
                except Exception:
                    pass
        for session in list(self._sessions):
            try:
                session.close()
            except Exception:
                pass
        for driver in list(self._drivers):
            try:
                self._forget_driver(driver)
            except Exception:
                pass
        self._results.clear()
        self._sessions.clear()
