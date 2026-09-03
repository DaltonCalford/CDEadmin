##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Milvus 2.6.5 PyMilvus boundary, vector results, and administration."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from pgadmin.cdeadmin.sdk import PilotProviderError


REFERENCE_VERSION = '2.6.5'
MAX_RECORDS = 10000
MAX_PAGE_SIZE = 1000
_HOST = re.compile(
    r'^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?'
    r'|\[[0-9A-Fa-f:.]+\])$'
)
_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,254}$')


class MilvusClientError(PilotProviderError):
    """Milvus operation or client-boundary validation failed."""


class MilvusDependencyError(MilvusClientError):
    """The selected PyMilvus distribution is unavailable."""


@dataclass
class _Session:
    client: object
    route: dict[str, Any]
    last_observation: str = 'no-operation-observed'
    closed: bool = False

    def close(self):
        self.closed = True
        close = getattr(self.client, 'close', None)
        if callable(close):
            close()


@dataclass
class _Result:
    matches: list[dict[str, Any]]
    operation: str
    native: Any


def _mapping(value, label='request'):
    if not isinstance(value, Mapping):
        raise MilvusClientError(f'{label} must be an object')
    return copy.deepcopy(dict(value))


def _text(value, label, maximum=4096):
    if not isinstance(value, str) or not value.strip():
        raise MilvusClientError(f'{label} must not be empty')
    value = value.strip()
    if len(value.encode('utf-8')) > maximum or any(
        ord(character) < 32 for character in value
    ):
        raise MilvusClientError(f'{label} is invalid')
    return value


def _name(value, label='name'):
    value = _text(value, label, 255)
    if not _NAME.fullmatch(value):
        raise MilvusClientError(f'{label} is invalid')
    return value


def _integer(value, label, default, minimum, maximum):
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, int):
        raise MilvusClientError(f'{label} must be an integer')
    if not minimum <= value <= maximum:
        raise MilvusClientError(f'{label} is outside the admitted range')
    return value


class MilvusClientAdapter:
    """Optional-client adapter retaining all Milvus semantics in-provider."""

    ADMIN_OPERATIONS = {
        'cluster': frozenset({'inspect'}),
        'resource-group': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'database': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'collection': frozenset({
            'inspect', 'create', 'alter', 'rename', 'insert', 'update',
            'delete', 'drop'
        }),
        'field': frozenset({'inspect'}),
        'partition': frozenset({
            'inspect', 'create', 'insert', 'update', 'delete', 'drop'
        }),
        'vector-index': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'alias': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'load-state': frozenset({'inspect', 'execute'}),
        'compaction': frozenset({'inspect', 'execute'}),
        'user': frozenset({
            'inspect', 'create', 'alter', 'grant', 'revoke', 'drop'
        }),
        'role': frozenset({'inspect', 'create', 'grant', 'revoke', 'drop'}),
        'privilege': frozenset({'inspect', 'grant', 'revoke'}),
        'credential': frozenset({'inspect', 'alter'}),
    }

    def __init__(self, secret_acquirer=None, module=None, connector=None):
        self.secret_acquirer = secret_acquirer
        if module is None:
            try:
                module = importlib.import_module('pymilvus')
            except ImportError:
                module = None
        self.module = module
        self._connector = connector or (
            getattr(module, 'MilvusClient', None) if module else None
        )
        self._sessions = []
        self._lock = threading.RLock()

    @staticmethod
    def _route(request):
        route = _mapping(request.get('route'), 'route')
        forbidden = {'password', 'secret', 'token', 'credential'}
        if forbidden.intersection(key.casefold() for key in route):
            raise MilvusClientError('inline credentials are forbidden')
        admitted = {
            'route_id', 'host', 'port', 'database', 'user', 'username',
            'credential_reference_id', 'principal_reference', 'tls_mode',
            'credential_references', 'credential_kinds', 'credential_kind',
            'tls_ca_file', 'tls_certificate_file', 'tls_key_file',
            'server_name', 'connect_timeout', 'operation_timeout',
            'consistency_level', 'auth_kind', 'read_only', 'keep_alive',
        }
        unknown = sorted(set(route).difference(admitted))
        if unknown:
            raise MilvusClientError(
                f'route contains unknown fields: {unknown}')
        result = {
            'route_id': _text(route.get('route_id', 'direct'), 'route ID'),
            'host': _text(route.get('host'), 'host', 255),
            'port': _integer(route.get('port'), 'port', 19530, 1, 65535),
            'database': _name(route.get('database', 'default'), 'database'),
            'connect_timeout': _integer(
                route.get('connect_timeout'), 'connect timeout', 10, 1, 120
            ),
            'operation_timeout': _integer(
                route.get('operation_timeout'), 'operation timeout',
                30, 1, 3600
            ),
            'tls_mode': route.get('tls_mode', 'disable'),
            'auth_kind': route.get('auth_kind', 'none'),
            'consistency_level': route.get('consistency_level', 'Bounded'),
            'read_only': route.get('read_only', False),
            'keep_alive': route.get('keep_alive', True),
        }
        if not _HOST.fullmatch(result['host']):
            raise MilvusClientError('host is invalid')
        if result['tls_mode'] not in {
            'disable', 'require', 'verify-ca', 'verify-full'
        }:
            raise MilvusClientError('TLS mode is invalid')
        if result['auth_kind'] not in {'none', 'basic', 'token'}:
            raise MilvusClientError('authentication kind is invalid')
        if result['consistency_level'] not in {
            'Strong', 'Bounded', 'Session', 'Eventually', 'Customized'
        }:
            raise MilvusClientError('consistency level is invalid')
        if not isinstance(result['read_only'], bool):
            raise MilvusClientError('read_only must be true or false')
        if not isinstance(result['keep_alive'], bool):
            raise MilvusClientError('keep_alive must be true or false')
        for field in (
            'username', 'credential_reference_id', 'principal_reference',
            'tls_ca_file', 'tls_certificate_file', 'tls_key_file',
            'server_name',
        ):
            if route.get(field) is not None:
                result[field] = _text(route[field], field)
        if 'username' not in result and route.get('user'):
            result['username'] = _text(route['user'], 'username')
        references = route.get('credential_references') or {}
        if not isinstance(references, Mapping):
            raise MilvusClientError('credential references must be an object')
        references = dict(references)
        expected_kind = {
            'none': None, 'basic': 'database_password', 'token': 'api_token',
        }[result['auth_kind']]
        if route.get('credential_reference_id'):
            references.setdefault(
                route.get('credential_kind') or expected_kind,
                route['credential_reference_id'],
            )
        allowed = {expected_kind} - {None}
        if set(references) - allowed or not all(
            isinstance(value, str) and value.strip()
            for value in references.values()
        ):
            raise MilvusClientError(
                'credential kinds do not match authentication mode'
            )
        result['credential_references'] = references
        if result['auth_kind'] == 'basic' and not (
            result.get('username') and expected_kind in references and
            result.get('principal_reference')
        ):
            raise MilvusClientError(
                'authentication requires user and credential references'
            )
        if result['auth_kind'] == 'token' and not (
            expected_kind in references and result.get('principal_reference')
        ):
            raise MilvusClientError(
                'token authentication requires a credential reference'
            )
        if result['auth_kind'] == 'none' and references:
            raise MilvusClientError(
                'unauthenticated route cannot acquire a credential'
            )
        if bool(result.get('tls_certificate_file')) != bool(
            result.get('tls_key_file')
        ):
            raise MilvusClientError(
                'client certificate and key must be configured together'
            )
        return result

    def _with_credentials(self, route, callback, purpose='connect'):
        references = dict(route.get('credential_references') or {})
        if not references:
            return callback({})
        if not callable(self.secret_acquirer):
            raise MilvusClientError('secret acquisition is unavailable')
        principal = route.get('principal_reference')
        if not principal:
            raise MilvusClientError(
                'credential reference requires a principal reference'
            )
        bindings = sorted(references.items())

        def acquire(index, values):
            if index == len(bindings):
                return callback(values)
            kind, reference = bindings[index]
            lease = self.secret_acquirer(
                reference, principal, purpose, kind
            )
            with lease:
                return lease.use(lambda view: acquire(index + 1, {
                    **values, kind: bytes(view).decode('utf-8'),
                }))

        return acquire(0, {})

    def _connect(self, request):
        if not callable(self._connector):
            raise MilvusDependencyError(
                'PyMilvus is required for the Milvus provider'
            )
        route = self._route(request)
        arguments = {
            'uri': f'http://{route["host"]}:{route["port"]}',
            'db_name': route['database'],
            'timeout': route['connect_timeout'],
            'keep_alive': route['keep_alive'],
        }
        if route['tls_mode'] != 'disable':
            arguments['secure'] = True
        optional = {
            'server_pem_path': route.get('tls_ca_file'),
            'client_pem_path': route.get('tls_certificate_file'),
            'client_key_path': route.get('tls_key_file'),
            'server_name': route.get('server_name'),
        }
        arguments.update({key: value for key, value in optional.items()
                          if value is not None})

        def connect(secrets):
            values = copy.deepcopy(arguments)
            if route['auth_kind'] == 'basic':
                values['user'] = route['username']
                values['password'] = secrets['database_password']
            elif route['auth_kind'] == 'token':
                values['token'] = secrets['api_token']
            return self._connector(**values)

        try:
            client = self._with_credentials(route, connect)
            client.list_collections()
        except MilvusClientError:
            raise
        except Exception as exc:
            raise MilvusClientError(
                f'Milvus connection failed ({type(exc).__name__})'
            ) from None
        return client, route

    def runtime_identity(self, request, handle=None):
        temporary = handle is None
        if temporary:
            client, _route = self._connect(request)
        else:
            if not isinstance(handle, _Session) or handle.closed:
                raise MilvusClientError('Milvus session is unavailable')
            client = handle.client
        try:
            getter = getattr(client, 'get_server_version', None)
            if not callable(getter):
                raise MilvusClientError(
                    'PyMilvus client cannot prove server version'
                )
            version = str(getter()).removeprefix('v')
            if version != REFERENCE_VERSION:
                raise MilvusClientError(
                    'runtime did not prove exact Milvus 2.6.5 identity'
                )
            return {
                'engine_id': 'milvus', 'version': version,
                'build_id': f'Milvus {version}', 'protocol_id': 'grpc',
            }
        finally:
            if temporary:
                close = getattr(client, 'close', None)
                if callable(close):
                    close()

    def open_session(self, request):
        client, route = self._connect(request)
        session = _Session(client, route)
        with self._lock:
            self._sessions.append(session)
        return session

    @staticmethod
    def describe_transaction(handle):
        if not isinstance(handle, _Session) or handle.closed:
            raise MilvusClientError('Milvus session is unavailable')
        return {
            'native_observation': handle.last_observation,
            'consistency_level': handle.route['consistency_level'],
            'multi_operation_transaction_supported': False,
            'automatic_replay': False,
            'finality_interpreted_by_common_code': False,
        }

    def execute(self, handle, request):
        if not isinstance(handle, _Session) or handle.closed:
            raise MilvusClientError('Milvus session is unavailable')
        source = request.get('source')
        if isinstance(source, str):
            try:
                source = json.loads(source)
            except json.JSONDecodeError as exc:
                raise MilvusClientError(
                    'Milvus query must be a JSON object'
                ) from exc
        document = _mapping(source, 'Milvus query')
        operation = str(document.pop('operation', 'search')).casefold()
        if operation not in {'search', 'hybrid_search', 'query', 'get'}:
            raise MilvusClientError('Milvus query operation is invalid')
        if 'collection_name' not in document:
            raise MilvusClientError('collection_name is required')
        document['collection_name'] = _name(
            document['collection_name'], 'collection'
        )
        document.setdefault('timeout', handle.route['operation_timeout'])
        document.setdefault(
            'consistency_level', handle.route['consistency_level']
        )
        try:
            result = getattr(handle.client, operation)(**document)
        except Exception as exc:
            raise MilvusClientError(
                f'Milvus {operation} failed ({type(exc).__name__})'
            ) from None
        matches = self._normalize_matches(result)
        handle.last_observation = f'native-{operation}-response-observed'
        return _Result(matches[:MAX_RECORDS], operation, result)

    @classmethod
    def _normalize_matches(cls, value, query_index=None):
        rows = []
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, list):
                    rows.extend(cls._normalize_matches(item, index))
                elif isinstance(item, Mapping):
                    row = copy.deepcopy(dict(item))
                    if query_index is not None:
                        row.setdefault('query_vector_index', query_index)
                    rows.append(row)
                else:
                    rows.append({'value': cls._json_value(item),
                                 'query_vector_index': query_index})
        elif isinstance(value, Mapping):
            rows.append(copy.deepcopy(dict(value)))
        elif value is not None:
            rows.append({'value': cls._json_value(value)})
        return rows

    @classmethod
    def _json_value(cls, value, depth=0):
        if depth > 24:
            return repr(value)
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, Mapping):
            return {str(key): cls._json_value(item, depth + 1)
                    for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_value(item, depth + 1) for item in value]
        to_dict = getattr(value, 'to_dict', None)
        if callable(to_dict):
            return cls._json_value(to_dict(), depth + 1)
        return str(value)

    @staticmethod
    def cancel(_token):
        return False

    @classmethod
    def describe_result(cls, token):
        if not isinstance(token, _Result):
            raise MilvusClientError('Milvus result token is invalid')
        return {
            'result_kind': 'vector',
            'schema': {'operation': token.operation,
                       'fields': sorted({key for row in token.matches
                                         for key in row})},
            'complete': True, 'stream_reference': None,
            'payload': {'matches': copy.deepcopy(token.matches),
                        'native': cls._json_value(token.native)},
        }

    @staticmethod
    def _generation(value):
        return hashlib.sha256(json.dumps(
            MilvusClientAdapter._json_value(value), sort_keys=True,
            separators=(',', ':')
        ).encode('utf-8')).hexdigest()[:24]

    def _resource(self, kind, name, native, path=None):
        path = path or [kind, name]
        return {
            'resource_id': 'milvus:' + ':'.join(map(str, path)),
            'resource_kind': kind, 'display_name': str(name),
            'authority_path': ['milvus', *map(str, path)],
            'display_path': list(map(str, path)),
            'generation': self._generation(native),
            'native': self._json_value(native),
        }

    @staticmethod
    def _call(client, method, default, *args, **kwargs):
        callback = getattr(client, method, None)
        if not callable(callback):
            return copy.deepcopy(default)
        try:
            return callback(*args, **kwargs)
        except Exception:
            return copy.deepcopy(default)

    def list_resources(self, request):
        client, route = self._connect(request)
        try:
            resources = [self._resource(
                'cluster', route['host'],
                {'host': route['host'], 'port': route['port']},
                ['cluster', route['host']],
            )]
            databases = self._call(
                client, 'list_databases', [route['database']]
            )
            for database in databases:
                resources.append(self._resource(
                    'database', database, {'name': database},
                    ['database', database]
                ))
            groups = self._call(client, 'list_resource_groups', [])
            for group in groups:
                resources.append(self._resource(
                    'resource-group', group, {'name': group},
                    ['resource-group', group]
                ))
            collections = client.list_collections()
            for collection in collections:
                description = self._call(
                    client, 'describe_collection', {
                        'collection_name': collection},
                    collection_name=collection,
                )
                resources.append(self._resource(
                    'collection', collection, description,
                    ['collection', collection]
                ))
                for field in description.get('fields', []) if isinstance(
                        description, Mapping) else []:
                    name = field.get('name') or field.get('field_name')
                    if name:
                        resources.append(self._resource(
                            'field', name, {
                                'collection_name': collection, **field},
                            ['field', collection, name]
                        ))
                for partition in self._call(
                    client, 'list_partitions', [], collection_name=collection
                ):
                    resources.append(self._resource(
                        'partition', partition,
                        {'collection_name': collection, 'name': partition},
                        ['partition', collection, partition]
                    ))
                for index in self._call(
                    client, 'list_indexes', [], collection_name=collection
                ):
                    native = self._call(
                        client, 'describe_index', {'index_name': index},
                        collection_name=collection, index_name=index,
                    )
                    resources.append(self._resource(
                        'vector-index', index,
                        {'collection_name': collection, **native},
                        ['vector-index', collection, index]
                    ))
                state = self._call(
                    client, 'get_load_state', {'state': 'unknown'},
                    collection_name=collection,
                )
                resources.append(self._resource(
                    'load-state', collection,
                    {'collection_name': collection, 'state': state},
                    ['load-state', collection]
                ))
                for alias in self._call(
                    client, 'list_aliases', [], collection_name=collection
                ):
                    resources.append(self._resource(
                        'alias', alias,
                        {'name': alias, 'collection_name': collection},
                        ['alias', collection, alias]
                    ))
            for user in self._call(client, 'list_users', []):
                name = user.get('user_name') if isinstance(
                    user, Mapping) else user
                resources.append(self._resource(
                    'user', name, self._json_value(user), ['user', name]
                ))
            for role in self._call(client, 'list_roles', []):
                name = role.get('role_name') if isinstance(
                    role, Mapping) else role
                resources.append(self._resource(
                    'role', name, self._json_value(role), ['role', name]
                ))
            resources.append(self._resource(
                'compaction', 'compaction', {'engine_owned': True}
            ))
            return resources
        finally:
            close = getattr(client, 'close', None)
            if callable(close):
                close()

    def inspect_resource(self, request):
        native = self._native_target(request)
        kind = request.get('resource_kind') or native.get('resource_kind')
        name = (native.get('name') or native.get('collection_name') or
                native.get('field_name') or native.get('index_name') or kind)
        if not kind or not name:
            raise MilvusClientError('Milvus resource identity is absent')
        return self._resource(kind, name, native)

    def describe_security(self, request):
        client, _route = self._connect(request)
        try:
            users = self._call(client, 'list_users', [])
            roles = self._call(client, 'list_roles', [])
            native = {
                'authorization_model': 'milvus-rbac',
                'users': self._json_value(users),
                'roles': self._json_value(roles),
                'credential_material_exposed': False,
            }
            return {
                'resource_id': 'milvus:security:rbac',
                'display_name': 'Milvus RBAC',
                'authority_path': ['milvus', 'security', 'rbac'],
                'generation': self._generation(native), 'native': native,
            }
        finally:
            close = getattr(client, 'close', None)
            if callable(close):
                close()

    def supports_admin_operation(self, resource_kind, operation_id):
        return operation_id in self.ADMIN_OPERATIONS.get(
            resource_kind, frozenset()
        )

    @staticmethod
    def _field(field_id, label, control='text', required=False, **values):
        return {'field_id': field_id, 'label': label, 'control': control,
                'required': required, **values}

    @classmethod
    def _form(cls, kind, operation):
        f = cls._field
        if operation == 'inspect':
            return {'form_id': 'milvus-inspect',
                    'title': 'Inspect', 'fields': []}
        if kind == 'collection' and operation == 'create':
            return {'form_id': 'milvus-collection-create',
                    'title': 'Create collection', 'fields': [
                        f('name', 'Collection name', required=True),
                        f('dimension', 'Vector dimension',
                          'number', True, default=128),
                        f('primary_field_name', 'Primary field',
                          required=True, default='id'),
                        f('vector_field_name', 'Vector field',
                          required=True, default='vector'),
                        f('metric_type', 'Metric', 'select', True,
                          default='COSINE',
                          options=[{'value': value, 'label': value}
                                   for value in ('COSINE', 'L2', 'IP')]),
                        f('auto_id', 'Automatic primary IDs',
                          'boolean', True, default=False),
                        f('enable_dynamic_field', 'Dynamic fields',
                          'boolean', True, default=True),
                        f('schema', 'Advanced schema (optional)', 'json',
                          False),
                    ]}
        if kind in {'collection', 'partition'} and operation in {
                'insert', 'update'}:
            return {'form_id': f'milvus-entity-{operation}',
                    'title': f'{operation.title()} entities', 'fields': [
                        f('collection_name', 'Collection', required=True),
                        f('partition_name', 'Partition'),
                        f('data', 'Entities', 'json', True, default=[]),
            ]}
        if kind in {'collection', 'partition'} and operation == 'delete':
            return {'form_id': 'milvus-entity-delete',
                    'title': 'Delete entities',
                    'fields': [
                        f('collection_name', 'Collection', required=True),
                        f('partition_name', 'Partition'),
                        f('filter', 'Filter expression', 'code'),
                        f('ids', 'Primary IDs', 'json'),
                        f('acknowledge_delete', 'Confirm delete', 'boolean',
                          True,
                          default=False),
                    ]}
        if kind == 'vector-index' and operation in {'create', 'alter'}:
            return {'form_id': f'milvus-index-{operation}',
                    'title': f'{operation.title()} vector index', 'fields': [
                        f('collection_name', 'Collection', required=True),
                        f('field_name', 'Vector field', required=True),
                        f('index_name', 'Index name', required=True),
                        f('index_type', 'Index type',
                          required=True, default='AUTOINDEX'),
                        f('metric_type', 'Metric', required=True,
                          default='COSINE'),
                        f('params', 'Index parameters', 'json', True,
                          default={}),
            ]}
        if kind == 'partition' and operation == 'create':
            return {'form_id': 'milvus-partition-create',
                    'title': 'Create partition', 'fields': [
                        f('collection_name', 'Collection', required=True),
                        f('name', 'Partition name', required=True),
                    ]}
        if kind == 'alias' and operation in {'create', 'alter'}:
            return {'form_id': f'milvus-alias-{operation}',
                    'title': f'{operation.title()} alias', 'fields': [
                        f('collection_name', 'Collection', required=True),
                        f('name', 'Alias', required=True),
            ]}
        if kind == 'load-state' and operation == 'execute':
            return {'form_id': 'milvus-load-execute',
                    'title': 'Change load state',
                    'fields': [
                        f('action', 'Action', 'select', True, default='load',
                          options=[{'value': 'load', 'label': 'Load'},
                                   {'value': 'release', 'label': 'Release'}]),
                        f('replica_number', 'Replica count',
                          'number', True, default=1),
                        f('acknowledge_operation', 'Confirm operation',
                          'boolean',
                          True, default=False),
                    ]}
        if kind == 'compaction' and operation == 'execute':
            return {'form_id': 'milvus-compaction-execute',
                    'title': 'Compact collection', 'fields': [
                        f('collection_name', 'Collection', required=True),
                        f('acknowledge_operation', 'Confirm operation',
                          'boolean',
                          True, default=False),
                    ]}
        if kind in {'database',
                    'resource-group'} and operation in {'create', 'alter'}:
            return {'form_id': f'milvus-{kind}-{operation}',
                    'title': f'{operation.title()} {kind}', 'fields': [
                        f('name', 'Name', required=operation == 'create'),
                        f('properties', 'Properties', 'json', True,
                          default={}),
            ]}
        if kind in {'user', 'credential'} and operation == 'create':
            return {'form_id': f'milvus-{kind}-{operation}',
                    'title': f'{operation.title()} {kind}', 'fields': [
                        f('name', 'User name', required=operation == 'create'),
                        f('password_reference', 'Password secret reference',
                          'secret-reference', True, sensitive=True),
            ]}
        if kind in {'user', 'credential'} and operation == 'alter':
            return {'form_id': f'milvus-{kind}-{operation}',
                    'title': f'{operation.title()} {kind}', 'fields': [
                        f('name', 'User name'),
                        f('current_password_reference',
                          'Current password secret reference',
                          'secret-reference', True, sensitive=True),
                        f('new_password_reference',
                          'New password secret reference',
                          'secret-reference', True, sensitive=True),
            ]}
        if kind in {'user', 'role', 'privilege'} and operation in {
                'grant', 'revoke'}:
            return {'form_id': f'milvus-rbac-{operation}',
                    'title': f'{operation.title()} RBAC binding', 'fields': [
                        f('user_name', 'User'), f(
                            'role_name', 'Role', required=True),
                        f('object_type', 'Object type'), f(
                            'object_name', 'Object name'),
                        f('privilege', 'Privilege'),
            ]}
        if operation == 'create':
            return {'form_id': f'milvus-{kind}-create',
                    'title': f'Create {kind}', 'fields': [
                        f('name', 'Name', required=True)
            ]}
        if operation == 'rename':
            return {'form_id': 'milvus-rename', 'title': 'Rename', 'fields': [
                f('new_name', 'New name', required=True)
            ]}
        if operation == 'drop':
            return {'form_id': 'milvus-drop', 'title': 'Drop', 'fields': [
                f('acknowledge_drop', 'Confirm drop', 'boolean', True,
                  default=False)
            ]}
        return {'form_id': f'milvus-{kind}-{operation}',
                'title': operation.title(), 'fields': [
                    f('properties', 'Properties', 'json', True, default={})
        ]}

    def visual_admin_catalog(self, catalog):
        catalog['native_planner'] = 'pymilvus-structured-planner'
        catalog['query_language'] = 'Milvus query/search JSON'
        catalog['consistency_authority'] = 'milvus'
        catalog['common_finality_interpretation'] = False
        for resource in catalog.get('objects', []):
            kind = resource['resource_kind']
            resource['operations'] = [
                operation for operation in resource.get(
                    'operations', []) if self.supports_admin_operation(
                    kind, operation['operation_id'])]
            for operation in resource['operations']:
                operation['form'] = self._form(kind, operation['operation_id'])
                if operation['operation_id'] in {'drop', 'delete', 'execute'}:
                    operation['confirmation_required'] = True
        return catalog

    def validate_admin_operation(self, request):
        errors = []
        try:
            kind, operation = request.get(
                'resource_kind'), request.get('operation_id')
            if not self.supports_admin_operation(kind, operation):
                raise MilvusClientError('operation is unavailable')
            draft = _mapping(request.get('draft', {}), 'draft')
            if operation == 'drop' and not draft.get('acknowledge_drop'):
                raise MilvusClientError('drop acknowledgement is required')
            if operation == 'delete' and not draft.get('acknowledge_delete'):
                raise MilvusClientError('delete acknowledgement is required')
            if operation == 'execute' and not draft.get(
                    'acknowledge_operation'):
                raise MilvusClientError(
                    'operation acknowledgement is required')
            if kind == 'collection' and operation == 'create':
                schema = draft.get('schema')
                if schema is None:
                    _integer(
                        draft.get('dimension'), 'dimension', 128, 1, 65535
                    )
                else:
                    self._validate_schema(schema)
        except MilvusClientError as exc:
            errors.append({'field_id': None,
                           'code': 'milvus_native_validation',
                           'message': str(exc)})
        return {'errors': errors, 'warnings': []}

    def plan_admin_operation(self, request):
        validation = self.validate_admin_operation(request)
        if validation['errors']:
            raise MilvusClientError(validation['errors'][0]['message'])
        return {
            'command_preview': {
                'provider': 'milvus',
                'resource_kind': request['resource_kind'],
                'operation': request['operation_id'],
                'target': self._safe_target(self._native_target(
                    request.get('target_resource')
                )), 'pymilvus_call_generated_at_execution': True,
            },
            'warnings': [],
            'provider_payload': {
                'kind': request['resource_kind'],
                'operation': request['operation_id'],
                'target': self._native_target(request.get('target_resource')),
                'draft': copy.deepcopy(request.get('draft', {})),
                'route': copy.deepcopy(request.get('_provider_route')),
            },
            'receipt': {'provider': 'milvus', 'automatic_retry': False,
                        'transaction_finality_interpreted': False},
        }

    def apply_admin_operation(self, request):
        payload = _mapping(request.get('provider_payload'), 'provider payload')
        client, route = self._connect({'route': payload.get('route')})
        try:
            result = self._apply_admin(client, route, payload)
            return {
                'native_response_observed': True,
                'native_response': self._json_value(result),
                'automatic_retry': False,
                'consistency_interpreted_by_common_code': False,
                'transaction_finality_interpreted_by_common_code': False,
            }
        finally:
            close = getattr(client, 'close', None)
            if callable(close):
                close()

    def _apply_admin(self, client, route, payload):
        kind, operation = payload['kind'], payload['operation']
        draft, target = payload.get('draft', {}), payload.get('target', {})
        name = (target.get('name') or target.get('collection_name') or
                draft.get('name'))
        if operation == 'inspect':
            return {'resource_kind': kind, 'target': self._safe_target(target)}
        if route['read_only']:
            raise MilvusClientError('read-only route refused mutation')
        if kind == 'database':
            if operation == 'create':
                return client.create_database(
                    db_name=_name(name),
                    properties=draft.get('properties') or {},
                )
            if operation == 'alter':
                return client.alter_database_properties(
                    db_name=_name(name),
                    properties=draft.get('properties') or {},
                )
            return client.drop_database(db_name=_name(name))
        if kind == 'resource-group':
            if operation == 'create':
                return client.create_resource_group(
                    resource_group=_name(name),
                    config=draft.get('properties') or {})
            if operation == 'alter':
                return client.update_resource_groups(
                    configs={_name(name): draft.get('properties') or {}}
                )
            return client.drop_resource_group(resource_group=_name(name))
        if kind == 'collection':
            collection = _name(
                name or draft.get('collection_name'),
                'collection')
            if operation == 'create':
                schema_definition = draft.get('schema')
                if schema_definition is not None:
                    arguments = {
                        'collection_name': collection,
                        'schema': self._build_schema(
                            client, schema_definition, draft
                        ),
                    }
                else:
                    arguments = {
                        'collection_name': collection,
                        'dimension': _integer(
                            draft.get('dimension'), 'dimension', 128, 1, 65535
                        ),
                        'primary_field_name': _name(
                            draft.get('primary_field_name', 'id'),
                            'primary field'
                        ),
                        'vector_field_name': _name(
                            draft.get('vector_field_name', 'vector'),
                            'vector field'
                        ),
                        'metric_type': draft.get('metric_type', 'COSINE'),
                        'auto_id': bool(draft.get('auto_id', False)),
                        'enable_dynamic_field': bool(
                            draft.get('enable_dynamic_field', True)
                        ),
                    }
                return client.create_collection(**arguments)
            if operation == 'alter':
                return client.alter_collection_properties(
                    collection_name=collection,
                    properties=draft.get('properties') or {},
                )
            if operation == 'rename':
                return client.rename_collection(
                    old_name=collection,
                    new_name=_name(draft['new_name'], 'new collection name'),
                )
            if operation == 'drop':
                return client.drop_collection(collection_name=collection)
        if kind in {'collection', 'partition'} and operation in {
                'insert', 'update', 'delete'}:
            collection = _name(
                draft.get('collection_name') or target.get('collection_name'),
                'collection'
            )
            arguments = {'collection_name': collection}
            partition = draft.get('partition_name')
            if kind == 'partition' and not partition:
                partition = target.get('name')
            if partition:
                arguments['partition_name'] = _name(partition, 'partition')
            if operation in {'insert', 'update'}:
                data = draft.get('data')
                if not isinstance(data, list) or not data:
                    raise MilvusClientError(
                        'entity data must be a non-empty array')
                arguments['data'] = copy.deepcopy(data)
                return getattr(client, 'upsert' if operation ==
                               'update' else 'insert')(**arguments)
            if draft.get('filter'):
                arguments['filter'] = _text(draft['filter'], 'filter', 65536)
            elif draft.get('ids') is not None:
                if not isinstance(draft['ids'], list):
                    raise MilvusClientError('ids must be an array')
                arguments['ids'] = copy.deepcopy(draft['ids'])
            else:
                raise MilvusClientError(
                    'delete requires a filter or primary IDs')
            return client.delete(**arguments)
        if kind == 'partition':
            collection = _name(
                draft.get('collection_name') or
                target.get('collection_name'),
                'collection',
            )
            partition = _name(name, 'partition')
            if operation == 'create':
                return client.create_partition(collection_name=collection,
                                               partition_name=partition)
            return client.drop_partition(collection_name=collection,
                                         partition_name=partition)
        if kind == 'vector-index':
            collection = _name(
                draft.get('collection_name') or
                target.get('collection_name'),
                'collection',
            )
            index_name = _name(
                draft.get('index_name') or
                target.get('index_name') or name,
                'index',
            )
            if operation == 'drop' or operation == 'alter':
                result = client.drop_index(collection_name=collection,
                                           index_name=index_name)
                if operation == 'drop':
                    return result
            params = client.prepare_index_params()
            params.add_index(
                field_name=_name(draft['field_name'], 'field'),
                index_name=index_name, index_type=_text(
                    draft['index_type'], 'index type'),
                metric_type=_text(draft['metric_type'], 'metric type'),
                params=_mapping(draft.get('params', {}), 'index parameters'),
            )
            return client.create_index(collection_name=collection,
                                       index_params=params)
        if kind == 'alias':
            alias = _name(name, 'alias')
            collection = _name(
                draft.get('collection_name') or
                target.get('collection_name'),
                'collection',
            )
            if operation == 'create':
                return client.create_alias(
                    collection_name=collection, alias=alias)
            if operation == 'alter':
                return client.alter_alias(
                    collection_name=collection, alias=alias)
            return client.drop_alias(alias=alias)
        if kind == 'load-state' and operation == 'execute':
            collection = _name(
                target.get('collection_name') or name,
                'collection')
            if draft['action'] == 'load':
                return client.load_collection(
                    collection_name=collection,
                    replica_number=_integer(
                        draft.get('replica_number'), 'replica count',
                        1, 1, 1024,
                    ),
                )
            return client.release_collection(collection_name=collection)
        if kind == 'compaction' and operation == 'execute':
            return client.compact(
                collection_name=_name(draft['collection_name'], 'collection')
            )
        if kind in {'user', 'credential'} and operation in {'create', 'alter'}:
            user = _name(name, 'user')
            if operation == 'create':
                password = self._admin_secret(
                    draft['password_reference'], route
                )
                return client.create_user(user_name=user, password=password)
            current_password = self._admin_secret(
                draft['current_password_reference'], route
            )
            new_password = self._admin_secret(
                draft['new_password_reference'], route
            )
            return client.update_password(
                user_name=user, old_password=current_password,
                new_password=new_password
            )
        if kind == 'user' and operation in {'grant', 'revoke'}:
            callback = (
                client.grant_role if operation == 'grant'
                else client.revoke_role
            )
            return callback(
                user_name=_name(
                    draft.get('user_name') or name, 'user'),
                role_name=_name(draft['role_name'], 'role'),
            )
        if kind == 'user' and operation == 'drop':
            return client.drop_user(user_name=_name(name, 'user'))
        if kind == 'role':
            role = _name(name or draft.get('role_name'), 'role')
            if operation == 'create':
                return client.create_role(role_name=role)
            if operation == 'drop':
                return client.drop_role(role_name=role)
        if kind in {'role', 'privilege'} and operation in {'grant', 'revoke'}:
            callback = (client.grant_privilege if operation == 'grant'
                        else client.revoke_privilege)
            return callback(
                role_name=_name(draft['role_name'], 'role'),
                object_type=_text(draft['object_type'], 'object type'),
                privilege=_text(draft['privilege'], 'privilege'),
                object_name=_text(draft['object_name'], 'object name'),
                db_name=route['database'],
            )
        raise MilvusClientError('administration operation is unavailable')

    def read_admin_rows(self, request):
        client, route = self._connect({
            'route': request.get('_provider_route')
        })
        try:
            target = self._native_target(request.get('target_resource'))
            collection = target.get('collection_name') or target.get('name')
            if not collection:
                raise MilvusClientError('collection target is invalid')
            limit = _integer(
                request.get('limit'), 'limit', 200, 1, MAX_PAGE_SIZE
            )
            continuation = request.get('continuation') or {}
            if not isinstance(continuation, Mapping):
                raise MilvusClientError('continuation is invalid')
            offset = _integer(
                continuation.get('offset'), 'offset', 0, 0, 16384
            )
            filter_value = request.get('filter')
            if isinstance(filter_value, Mapping):
                filter_value = filter_value.get('expression', '')
            filter_value = str(filter_value or '')
            result = client.query(
                collection_name=_name(collection, 'collection'),
                filter=filter_value, output_fields=['*'], limit=limit,
                offset=offset, timeout=route['operation_timeout'],
                consistency_level=route['consistency_level'],
            )
            records = self._normalize_matches(result)
            return {
                'records': self._json_value(records),
                'editable': not route['read_only'],
                'insertable': not route['read_only'],
                'continuation': (
                    {'offset': offset + len(records)}
                    if len(records) == limit else None
                ),
                'limits': {'maximum_page_size': MAX_PAGE_SIZE,
                           'maximum_offset': 16384},
                'provider_owned_identity': True,
            }
        except MilvusClientError:
            raise
        except Exception as exc:
            raise MilvusClientError(
                f'Milvus entity read failed ({type(exc).__name__})'
            ) from None
        finally:
            close = getattr(client, 'close', None)
            if callable(close):
                close()

    @staticmethod
    def cancel_admin_cursor(request):
        return {
            'cancelled': bool(request.get('continuation')),
            'provider_owned_cursor': False,
        }

    @staticmethod
    def _schema_fields(value):
        if isinstance(value, list):
            fields = value
        elif isinstance(value, Mapping):
            fields = value.get('fields')
        else:
            fields = None
        if not isinstance(fields, list) or not fields:
            raise MilvusClientError(
                'advanced schema requires a non-empty fields array'
            )
        return fields

    @classmethod
    def _validate_schema(cls, value):
        admitted = {
            'name', 'datatype', 'is_primary', 'auto_id', 'dim',
            'max_length', 'nullable', 'default_value', 'element_type',
            'max_capacity', 'is_partition_key', 'is_clustering_key',
        }
        for field in cls._schema_fields(value):
            field = _mapping(field, 'schema field')
            unknown = sorted(set(field).difference(admitted))
            if unknown:
                raise MilvusClientError(
                    f'schema field contains unknown properties: {unknown}'
                )
            _name(field.get('name'), 'schema field')
            _text(field.get('datatype'), 'schema field datatype', 64)

    def _build_schema(self, client, definition, draft):
        self._validate_schema(definition)
        creator = getattr(client, 'create_schema', None)
        data_types = getattr(self.module, 'DataType', None)
        if not callable(creator) or data_types is None:
            raise MilvusDependencyError(
                'PyMilvus client cannot create an advanced schema'
            )
        options = definition if isinstance(definition, Mapping) else {}
        schema = creator(
            auto_id=bool(options.get('auto_id', draft.get('auto_id', False))),
            enable_dynamic_field=bool(options.get(
                'enable_dynamic_field',
                draft.get('enable_dynamic_field', True),
            )),
        )
        for raw_field in self._schema_fields(definition):
            field = _mapping(raw_field, 'schema field')
            type_name = _text(
                field.pop('datatype'), 'schema field datatype', 64
            ).upper()
            datatype = getattr(data_types, type_name, None)
            if datatype is None:
                raise MilvusClientError(
                    f'Milvus datatype {type_name!r} is unavailable'
                )
            name = _name(field.pop('name'), 'schema field')
            if 'element_type' in field:
                element_name = _text(
                    field['element_type'], 'element datatype', 64
                ).upper()
                field['element_type'] = getattr(data_types, element_name, None)
                if field['element_type'] is None:
                    raise MilvusClientError(
                        f'Milvus datatype {element_name!r} is unavailable'
                    )
            schema.add_field(
                field_name=name, datatype=datatype, **copy.deepcopy(field)
            )
        return schema

    def _admin_secret(self, reference, route):
        changed = copy.deepcopy(route)
        changed['credential_reference_id'] = _text(
            reference, 'password secret reference'
        )
        return self._with_password(changed, lambda value: _text(
            value, 'resolved password', 65536
        ), purpose='administer')

    @staticmethod
    def _native_target(target):
        if not isinstance(target, Mapping):
            return {}
        extensions = target.get('extensions')
        if isinstance(extensions, Mapping):
            provider = extensions.get('milvus')
            if isinstance(provider, Mapping) and isinstance(
                provider.get('native'), Mapping
            ):
                return copy.deepcopy(dict(provider['native']))
        native = target.get('native')
        return copy.deepcopy(dict(native)) if isinstance(
            native, Mapping) else {}

    @staticmethod
    def _safe_target(target):
        return {key: copy.deepcopy(value) for key, value in target.items()
                if all(marker not in key.casefold()
                       for marker in ('password', 'secret', 'credential'))}

    def close(self):
        with self._lock:
            sessions, self._sessions = self._sessions, []
        for session in sessions:
            session.close()
