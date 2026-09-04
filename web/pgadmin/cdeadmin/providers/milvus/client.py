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
_VECTOR_INDEX_TYPES = (
    'AUTOINDEX', 'FLAT', 'IVF_FLAT', 'IVF_SQ8', 'IVF_PQ', 'HNSW',
    'SCANN', 'DISKANN', 'BIN_FLAT', 'BIN_IVF_FLAT',
    'SPARSE_INVERTED_INDEX', 'SPARSE_WAND', 'GPU_BRUTE_FORCE',
    'GPU_IVF_FLAT', 'GPU_IVF_PQ', 'GPU_CAGRA',
)
_METRIC_TYPES = (
    'COSINE', 'L2', 'IP', 'HAMMING', 'JACCARD', 'BM25',
)
_FIELD_TYPES = (
    'BOOL', 'INT8', 'INT16', 'INT32', 'INT64', 'FLOAT', 'DOUBLE',
    'VARCHAR', 'JSON', 'ARRAY', 'FLOAT_VECTOR', 'BINARY_VECTOR',
    'FLOAT16_VECTOR', 'BFLOAT16_VECTOR', 'SPARSE_FLOAT_VECTOR',
)


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


def _string_list(value, label, required=False):
    if value is None:
        value = []
    if not isinstance(value, list) or (required and not value):
        raise MilvusClientError(f'{label} must be an array')
    result = [_name(item, f'{label} item') for item in value]
    if len(result) != len(set(result)):
        raise MilvusClientError(f'{label} must not contain duplicates')
    return result


def _property_mode(value, label):
    value = value or 'unchanged'
    if value not in {'unchanged', 'enabled', 'disabled', 'clear'}:
        raise MilvusClientError(f'{label} mode is invalid')
    return value


def _json_literal(value, label):
    if value is None or not isinstance(value, str) or not value.strip():
        raise MilvusClientError(f'{label} must contain a JSON value')
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise MilvusClientError(f'{label} is not valid JSON') from exc


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
        'field': frozenset({'inspect', 'create', 'alter'}),
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
        list_fields = getattr(value, 'ListFields', None)
        if callable(list_fields):
            return {
                descriptor.name: cls._json_value(item, depth + 1)
                for descriptor, item in list_fields()
            }
        attributes = getattr(value, '__dict__', None)
        if isinstance(attributes, Mapping):
            return {
                str(key).removeprefix('_'): cls._json_value(
                    item, depth + 1
                )
                for key, item in attributes.items()
            }
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
    def _invoke(client, method, *args, **kwargs):
        callback = getattr(client, method, None)
        if not callable(callback):
            raise MilvusDependencyError(
                f'PyMilvus 2.6.5 does not provide {method}'
            )
        try:
            return callback(*args, **kwargs)
        except MilvusClientError:
            raise
        except Exception as exc:
            raise MilvusClientError(
                f'Milvus {method} failed ({type(exc).__name__})'
            ) from None

    def list_resources(self, request):
        client, route = self._connect(request)
        try:
            resources = [self._resource(
                'cluster', route['host'],
                {
                    'host': route['host'], 'port': route['port'],
                    'version': self._invoke(client, 'get_server_version'),
                },
                ['cluster', route['host']],
            )]
            databases = self._invoke(client, 'list_databases')
            for database in databases:
                native = self._invoke(
                    client, 'describe_database', db_name=database
                )
                resources.append(self._resource(
                    'database', database, {'name': database, **native},
                    ['database', database]
                ))
            groups = self._invoke(client, 'list_resource_groups')
            for group in groups:
                native = self._invoke(
                    client, 'describe_resource_group', name=group
                )
                resources.append(self._resource(
                    'resource-group', group,
                    {'name': group, **self._json_value(native)},
                    ['resource-group', group]
                ))
            for database in databases:
                self._invoke(client, 'use_database', db_name=database)
                collections = self._invoke(client, 'list_collections')
                for collection in collections:
                    description = self._invoke(
                        client, 'describe_collection',
                        collection_name=collection,
                    )
                    statistics = self._invoke(
                        client, 'get_collection_stats',
                        collection_name=collection,
                    )
                    native = {
                        **description, 'database': database,
                        'statistics': statistics,
                    }
                    resources.append(self._resource(
                        'collection', collection, native,
                        ['database', database, 'collection', collection]
                    ))
                    for field in description.get('fields', []):
                        name = field.get('name') or field.get('field_name')
                        if name:
                            resources.append(self._resource(
                                'field', name, {
                                    'database': database,
                                    'collection_name': collection, **field,
                                },
                                ['database', database, 'collection',
                                 collection, 'field', name]
                            ))
                    for partition in self._invoke(
                        client, 'list_partitions',
                        collection_name=collection,
                    ):
                        partition_stats = self._invoke(
                            client, 'get_partition_stats',
                            collection_name=collection,
                            partition_name=partition,
                        )
                        resources.append(self._resource(
                            'partition', partition, {
                                'database': database,
                                'collection_name': collection,
                                'name': partition,
                                'statistics': partition_stats,
                            },
                            ['database', database, 'collection', collection,
                             'partition', partition]
                        ))
                    for index in self._invoke(
                        client, 'list_indexes',
                        collection_name=collection,
                    ):
                        index_native = self._invoke(
                            client, 'describe_index',
                            collection_name=collection, index_name=index,
                        )
                        resources.append(self._resource(
                            'vector-index', index, {
                                'database': database,
                                'collection_name': collection,
                                **index_native,
                            },
                            ['database', database, 'collection', collection,
                             'vector-index', index]
                        ))
                    state = self._invoke(
                        client, 'get_load_state',
                        collection_name=collection,
                    )
                    resources.append(self._resource(
                        'load-state', collection, {
                            'database': database,
                            'collection_name': collection,
                            'state': state,
                        },
                        ['database', database, 'collection', collection,
                         'load-state']
                    ))
                    alias_listing = self._invoke(
                        client, 'list_aliases',
                        collection_name=collection,
                    )
                    aliases = (
                        alias_listing.get('aliases', [])
                        if isinstance(alias_listing, Mapping)
                        else alias_listing
                    )
                    if not isinstance(aliases, list):
                        raise MilvusClientError(
                            'Milvus alias listing has an invalid shape'
                        )
                    for alias in aliases:
                        alias_native = self._invoke(
                            client, 'describe_alias', alias=alias
                        )
                        resources.append(self._resource(
                            'alias', alias, {
                                'database': database,
                                'collection_name': collection,
                                'name': alias, **alias_native,
                            },
                            ['database', database, 'alias', alias]
                        ))
            self._invoke(client, 'use_database', db_name=route['database'])
            for user in self._invoke(client, 'list_users'):
                name = user.get('user_name') if isinstance(
                    user, Mapping) else user
                native = self._invoke(
                    client, 'describe_user', user_name=name
                )
                resources.append(self._resource(
                    'user', name, self._json_value(native), ['user', name]
                ))
                resources.append(self._resource(
                    'credential', name, {
                        'user_name': name,
                        'credential_material_exposed': False,
                    }, ['user', name, 'credential']
                ))
            for role in self._invoke(client, 'list_roles'):
                name = role.get('role_name') if isinstance(
                    role, Mapping) else role
                native = self._invoke(
                    client, 'describe_role', role_name=name
                )
                resources.append(self._resource(
                    'role', name, self._json_value(native), ['role', name]
                ))
                privileges = native.get('privileges', []) if isinstance(
                    native, Mapping
                ) else []
                for ordinal, privilege in enumerate(privileges):
                    privilege = self._json_value(privilege)
                    privilege_name = ':'.join(str(privilege.get(key, '*'))
                                              for key in (
                                                  'object_type',
                                                  'object_name', 'privilege',
                                              ))
                    resources.append(self._resource(
                        'privilege', privilege_name, {
                            'role_name': name, **privilege,
                        }, ['role', name, 'privilege', ordinal]
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
            users = self._invoke(client, 'list_users')
            roles = self._invoke(client, 'list_roles')
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

    @staticmethod
    def _property_options():
        return [
            {'value': 'unchanged', 'label': 'Leave unchanged'},
            {'value': 'enabled', 'label': 'Enabled'},
            {'value': 'disabled', 'label': 'Disabled'},
            {'value': 'clear', 'label': 'Clear property'},
        ]

    @staticmethod
    def _set_property_options():
        return [
            {'value': 'unchanged', 'label': 'Leave unchanged'},
            {'value': 'enabled', 'label': 'Enabled'},
            {'value': 'disabled', 'label': 'Disabled'},
        ]

    @staticmethod
    def _mode_change(draft, field, native_key, changes, clear):
        mode = _property_mode(draft.get(field), field)
        if mode in {'enabled', 'disabled'}:
            changes[native_key] = mode == 'enabled'
        elif mode == 'clear':
            clear.append(native_key)

    @classmethod
    def _database_property_changes(cls, draft):
        changes, clear = {}, []
        numeric = (
            ('max_collections', 'database.max.collections', 1, 65536),
            ('replica_number', 'database.replica.number', 1, 1024),
        )
        for field, native_key, minimum, maximum in numeric:
            if draft.get(field) is not None:
                changes[native_key] = str(_integer(
                    draft[field], field, minimum, minimum, maximum
                ))
            if draft.get('clear_' + field):
                if field in draft and draft.get(field) is not None:
                    raise MilvusClientError(
                        f'{field} cannot be set and cleared together'
                    )
                clear.append(native_key)
        groups = _string_list(
            draft.get('resource_groups', []), 'resource groups'
        )
        if groups:
            changes['database.resource_groups'] = ','.join(groups)
        if draft.get('clear_resource_groups'):
            if groups:
                raise MilvusClientError(
                    'resource groups cannot be set and cleared together'
                )
            clear.append('database.resource_groups')
        for field, native_key in (
            ('deny_writes_mode', 'database.force.deny.writing'),
            ('deny_reads_mode', 'database.force.deny.reading'),
            ('deny_ddl_mode', 'database.force.deny.ddl'),
        ):
            cls._mode_change(draft, field, native_key, changes, clear)
        if changes and clear:
            raise MilvusClientError(
                'database properties must be set or cleared in one request, '
                'not both'
            )
        return changes, clear

    @classmethod
    def _collection_property_changes(cls, draft):
        changes, clear = {}, []
        if draft.get('ttl_seconds') is not None:
            changes['collection.ttl.seconds'] = str(_integer(
                draft['ttl_seconds'], 'TTL seconds', 0, 0, 315360000
            ))
        if draft.get('clear_ttl'):
            if draft.get('ttl_seconds') is not None:
                raise MilvusClientError(
                    'TTL cannot be set and cleared together'
                )
            clear.append('collection.ttl.seconds')
        if draft.get('replica_number') is not None:
            changes['collection.replica.number'] = str(_integer(
                draft['replica_number'], 'replica number', 1, 1, 1024
            ))
        if draft.get('clear_replica_number'):
            if draft.get('replica_number') is not None:
                raise MilvusClientError(
                    'replica number cannot be set and cleared together'
                )
            clear.append('collection.replica.number')
        groups = _string_list(
            draft.get('resource_groups', []), 'resource groups'
        )
        if groups:
            changes['collection.resource_groups'] = ','.join(groups)
        if draft.get('clear_resource_groups'):
            if groups:
                raise MilvusClientError(
                    'resource groups cannot be set and cleared together'
                )
            clear.append('collection.resource_groups')
        cls._mode_change(
            draft, 'mmap_mode', 'mmap.enabled', changes, clear
        )
        cls._mode_change(
            draft, 'partition_key_isolation_mode',
            'partitionkey.isolation', changes, clear,
        )
        if changes and clear:
            raise MilvusClientError(
                'collection properties must be set or cleared in one '
                'request, not both'
            )
        if not changes and not clear:
            raise MilvusClientError(
                'collection alteration requires a property change'
            )
        return changes, clear

    @staticmethod
    def _index_parameters(draft):
        index_type = draft.get('index_type', 'AUTOINDEX')
        if index_type not in _VECTOR_INDEX_TYPES:
            raise MilvusClientError('index type is invalid')
        metric = draft.get('metric_type', 'COSINE')
        if metric not in _METRIC_TYPES:
            raise MilvusClientError('metric type is invalid')
        vector_type = draft.get('vector_data_type', 'FLOAT_VECTOR')
        if vector_type not in {
            'FLOAT_VECTOR', 'BINARY_VECTOR', 'FLOAT16_VECTOR',
            'BFLOAT16_VECTOR', 'SPARSE_FLOAT_VECTOR',
        }:
            raise MilvusClientError('vector field data type is invalid')
        binary_index = index_type in {'BIN_FLAT', 'BIN_IVF_FLAT'}
        sparse_index = index_type in {
            'SPARSE_INVERTED_INDEX', 'SPARSE_WAND'
        }
        if binary_index != (vector_type == 'BINARY_VECTOR'):
            raise MilvusClientError(
                'binary vectors and binary index types must be selected '
                'together'
            )
        if sparse_index != (vector_type == 'SPARSE_FLOAT_VECTOR'):
            raise MilvusClientError(
                'sparse vectors and sparse index types must be selected '
                'together'
            )
        if binary_index and metric not in {'HAMMING', 'JACCARD'}:
            raise MilvusClientError(
                'binary indexes require HAMMING or JACCARD distance'
            )
        if sparse_index and metric not in {'IP', 'BM25'}:
            raise MilvusClientError(
                'sparse indexes require IP or BM25 scoring'
            )
        if not binary_index and not sparse_index and metric not in {
            'COSINE', 'L2', 'IP'
        }:
            raise MilvusClientError(
                'dense vector indexes require COSINE, L2, or IP distance'
            )
        params = {}
        if index_type.startswith('IVF_') or index_type in {
            'SCANN', 'BIN_IVF_FLAT', 'GPU_IVF_FLAT', 'GPU_IVF_PQ'
        }:
            params['nlist'] = _integer(
                draft.get('nlist'), 'nlist', 1024, 1, 65536
            )
        if index_type == 'HNSW':
            params.update({
                'M': _integer(draft.get('hnsw_m'), 'HNSW M', 16, 2, 2048),
                'efConstruction': _integer(
                    draft.get('ef_construction'), 'efConstruction',
                    200, 1, 65536,
                ),
            })
        if index_type in {'IVF_PQ', 'GPU_IVF_PQ'}:
            params.update({
                'm': _integer(draft.get('pq_m'), 'PQ m', 4, 1, 65536),
                'nbits': _integer(
                    draft.get('nbits'), 'PQ nbits', 8, 1, 64
                ),
            })
        if index_type in {'SPARSE_INVERTED_INDEX', 'SPARSE_WAND'}:
            ratio = draft.get('drop_ratio_build', 0.0)
            if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) \
                    or not 0 <= ratio <= 1:
                raise MilvusClientError(
                    'sparse drop ratio must be between zero and one'
                )
            params['drop_ratio_build'] = ratio
        if index_type == 'SCANN':
            params['with_raw_data'] = bool(
                draft.get('with_raw_data', True)
            )
        if index_type == 'GPU_CAGRA':
            build_algorithm = draft.get('build_algo', 'IVF_PQ')
            if build_algorithm not in {'IVF_PQ', 'NN_DESCENT'}:
                raise MilvusClientError(
                    'CAGRA build algorithm is invalid'
                )
            params.update({
                'intermediate_graph_degree': _integer(
                    draft.get('intermediate_graph_degree'),
                    'intermediate graph degree', 64, 1, 4096,
                ),
                'graph_degree': _integer(
                    draft.get('graph_degree'), 'graph degree', 32, 1, 4096
                ),
                'build_algo': build_algorithm,
            })
        return index_type, metric, params

    def _field_type_options(self, draft, data_type):
        options = {
            'nullable': bool(draft.get('nullable', True)),
        }
        if draft.get('description'):
            options['desc'] = _text(
                draft.get('description'), 'field description', 4096
            )
        if draft.get('default_value_json') is not None:
            options['default_value'] = _json_literal(
                draft.get('default_value_json'), 'default value'
            )
        if data_type == 'VARCHAR':
            options['max_length'] = _integer(
                draft.get('max_length'), 'maximum string length',
                65535, 1, 65535,
            )
        if data_type == 'ARRAY':
            element = draft.get('element_type')
            if element not in _FIELD_TYPES or element == 'ARRAY' or (
                'VECTOR' in str(element)
            ):
                raise MilvusClientError('array element type is invalid')
            options['element_type'] = self._data_type(element)
            options['max_capacity'] = _integer(
                draft.get('max_capacity'), 'maximum array capacity',
                4096, 1, 4096,
            )
        if data_type in {
            'FLOAT_VECTOR', 'BINARY_VECTOR', 'FLOAT16_VECTOR',
            'BFLOAT16_VECTOR',
        }:
            options['dim'] = _integer(
                draft.get('dimension'), 'vector dimension',
                128, 1, 65535,
            )
        return options

    def _data_type(self, name):
        data_types = getattr(self.module, 'DataType', None)
        value = getattr(data_types, name, None) if data_types else None
        if value is None:
            raise MilvusDependencyError(
                f'PyMilvus 2.6.5 datatype {name!r} is unavailable'
            )
        return value

    def _resource_group_config(self, draft):
        requested = _integer(
            draft.get('requested_nodes'), 'requested nodes', 0, 0, 65536
        )
        limit = _integer(
            draft.get('limit_nodes'), 'limit nodes', 0, 0, 65536
        )
        if limit and requested > limit:
            raise MilvusClientError(
                'requested nodes cannot exceed the resource-group limit'
            )
        transfer_from = _string_list(
            draft.get('transfer_from', []), 'transfer from'
        )
        transfer_to = _string_list(
            draft.get('transfer_to', []), 'transfer to'
        )
        types = getattr(getattr(self.module, 'client', None), 'types', None)
        factory = getattr(types, 'ResourceGroupConfig', None)
        if not callable(factory):
            raise MilvusDependencyError(
                'PyMilvus 2.6.5 resource-group configuration is unavailable'
            )
        return factory(
            requests={'node_num': requested}, limits={'node_num': limit},
            transfer_from=[{'resource_group': value}
                           for value in transfer_from],
            transfer_to=[{'resource_group': value}
                         for value in transfer_to],
        )

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
                        f('primary_id_type', 'Primary ID type', 'select',
                          True, default='int', options=[
                              {'value': 'int', 'label': '64-bit integer'},
                              {'value': 'string', 'label': 'String'},
                          ]),
                        f('primary_max_length',
                          'Maximum primary string length', 'number', False,
                          minimum=1, maximum=65535),
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
        if kind == 'collection' and operation == 'alter':
            return {
                'form_id': 'milvus-collection-alter',
                'title': 'Alter collection properties',
                'fields': [
                    f('ttl_seconds', 'Entity TTL (seconds)', 'number', False,
                      minimum=0, maximum=315360000),
                    f('mmap_mode', 'Memory mapping', 'select', True,
                      default='unchanged', options=cls._property_options()),
                    f('partition_key_isolation_mode',
                      'Partition-key isolation', 'select', True,
                      default='unchanged', options=cls._property_options()),
                    f('replica_number', 'Default replica count', 'number',
                      False, minimum=1, maximum=1024),
                    f('resource_groups', 'Default resource groups', 'json',
                      False, default=[]),
                    f('clear_ttl', 'Clear entity TTL', 'boolean', True,
                      default=False),
                    f('clear_replica_number', 'Clear replica count',
                      'boolean', True, default=False),
                    f('clear_resource_groups', 'Clear resource groups',
                      'boolean', True, default=False),
                ],
            }
        if kind == 'field' and operation == 'create':
            return {
                'form_id': 'milvus-field-create',
                'title': 'Add collection field',
                'fields': [
                    f('collection_name', 'Collection', required=True),
                    f('name', 'Field name', required=True),
                    f('data_type', 'Data type', 'select', True,
                      options=[{'value': value, 'label': value}
                               for value in _FIELD_TYPES]),
                    f('description', 'Description'),
                    f('nullable', 'Nullable', 'boolean', True, default=True),
                    f('default_value_json', 'Default value (JSON literal)',
                      'text', False),
                    f('max_length', 'Maximum string length', 'number', False,
                      minimum=1, maximum=65535),
                    f('element_type', 'Array element type', 'select', False,
                      options=[{'value': value, 'label': value}
                               for value in _FIELD_TYPES
                               if 'VECTOR' not in value and value != 'ARRAY']),
                    f('max_capacity', 'Maximum array capacity', 'number',
                      False, minimum=1, maximum=4096),
                    f('dimension', 'Vector dimension', 'number', False,
                      minimum=1, maximum=65535),
                ],
            }
        if kind == 'field' and operation == 'alter':
            return {
                'form_id': 'milvus-field-alter',
                'title': 'Alter field properties',
                'fields': [
                    f('mmap_mode', 'Memory mapping', 'select', True,
                      default='unchanged',
                      options=cls._set_property_options()),
                ],
            }
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
        if kind == 'vector-index' and operation == 'create':
            return {'form_id': 'milvus-index-create',
                    'title': 'Create vector index', 'fields': [
                        f('collection_name', 'Collection', required=True),
                        f('field_name', 'Vector field', required=True),
                        f('index_name', 'Index name', required=True),
                        f('vector_data_type', 'Vector field data type',
                          'select', True, default='FLOAT_VECTOR', options=[
                              {'value': value, 'label': value}
                              for value in (
                                  'FLOAT_VECTOR', 'BINARY_VECTOR',
                                  'FLOAT16_VECTOR', 'BFLOAT16_VECTOR',
                                  'SPARSE_FLOAT_VECTOR',
                              )
                          ]),
                        f('index_type', 'Index type', 'select', True,
                          default='AUTOINDEX', options=[
                              {'value': value, 'label': value}
                              for value in _VECTOR_INDEX_TYPES
                          ]),
                        f('metric_type', 'Metric', 'select', True,
                          default='COSINE', options=[
                              {'value': value, 'label': value}
                              for value in _METRIC_TYPES
                          ]),
                        f('nlist', 'IVF cluster count', 'number', False,
                          minimum=1, maximum=65536),
                        f('hnsw_m', 'HNSW connections', 'number', False,
                          minimum=2, maximum=2048),
                        f('ef_construction', 'HNSW build breadth', 'number',
                          False, minimum=1, maximum=65536),
                        f('pq_m', 'PQ subquantizers', 'number', False,
                          minimum=1, maximum=65536),
                        f('nbits', 'PQ bits', 'number', False,
                          minimum=1, maximum=64),
                        f('drop_ratio_build', 'Sparse drop ratio', 'number',
                          False, minimum=0, maximum=1),
                        f('with_raw_data', 'SCANN retains raw vectors',
                          'boolean', False),
                        f('intermediate_graph_degree',
                          'CAGRA intermediate graph degree', 'number', False,
                          minimum=1, maximum=4096),
                        f('graph_degree', 'CAGRA graph degree', 'number',
                          False, minimum=1, maximum=4096),
                        f('build_algo', 'CAGRA build algorithm', 'select',
                          False, options=[
                              {'value': 'IVF_PQ', 'label': 'IVF_PQ'},
                              {'value': 'NN_DESCENT', 'label': 'NN_DESCENT'},
                          ]),
                    ]}
        if kind == 'vector-index' and operation == 'alter':
            return {
                'form_id': 'milvus-index-alter',
                'title': 'Alter vector index properties',
                'fields': [
                    f('mmap_mode', 'Memory mapping', 'select', True,
                      default='unchanged', options=cls._property_options()),
                ],
            }
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
                        f('resource_groups', 'Resource groups', 'json', False,
                          default=[]),
                        f('load_fields', 'Fields to load', 'json', False,
                          default=[]),
                        f('skip_dynamic_field', 'Skip dynamic field',
                          'boolean', True, default=False),
                        f('refresh', 'Refresh an existing load', 'boolean',
                          True, default=False),
                        f('acknowledge_operation', 'Confirm operation',
                          'boolean',
                          True, default=False),
                    ]}
        if kind == 'compaction' and operation == 'execute':
            return {'form_id': 'milvus-compaction-execute',
                    'title': 'Compact collection', 'fields': [
                        f('collection_name', 'Collection', required=True),
                        f('compaction_kind', 'Compaction kind', 'select', True,
                          default='merge', options=[
                              {'value': 'merge', 'label': 'Merge'},
                              {'value': 'clustering', 'label': 'Clustering'},
                              {'value': 'level-zero', 'label': 'Level zero'},
                          ]),
                        f('acknowledge_operation', 'Confirm operation',
                          'boolean',
                          True, default=False),
                    ]}
        if kind == 'database' and operation in {'create', 'alter'}:
            return {
                'form_id': f'milvus-database-{operation}',
                'title': f'{operation.title()} database',
                'fields': [
                    f('name', 'Name', required=operation == 'create'),
                    f('max_collections', 'Maximum collections', 'number',
                      False, minimum=1, maximum=65536),
                    f('replica_number', 'Default replica count', 'number',
                      False, minimum=1, maximum=1024),
                    f('resource_groups', 'Default resource groups', 'json',
                      False, default=[]),
                    f('deny_writes_mode', 'Deny writes', 'select', True,
                      default='unchanged', options=cls._property_options()),
                    f('deny_reads_mode', 'Deny reads', 'select', True,
                      default='unchanged', options=cls._property_options()),
                    f('deny_ddl_mode', 'Deny DDL', 'select', True,
                      default='unchanged', options=cls._property_options()),
                    f('clear_max_collections', 'Clear maximum collections',
                      'boolean', True, default=False),
                    f('clear_replica_number', 'Clear replica count',
                      'boolean', True, default=False),
                    f('clear_resource_groups', 'Clear resource groups',
                      'boolean', True, default=False),
                ],
            }
        if kind == 'resource-group' and operation == 'create':
            return {
                'form_id': 'milvus-resource-group-create',
                'title': 'Create resource group',
                'fields': [f('name', 'Name', required=True)],
            }
        if kind == 'resource-group' and operation == 'alter':
            return {
                'form_id': 'milvus-resource-group-alter',
                'title': 'Configure resource group',
                'fields': [
                    f('requested_nodes', 'Requested query nodes', 'number',
                      True, default=0, minimum=0, maximum=65536),
                    f('limit_nodes', 'Maximum query nodes', 'number', True,
                      default=0, minimum=0, maximum=65536),
                    f('transfer_from', 'Transfer nodes from', 'json', False,
                      default=[]),
                    f('transfer_to', 'Transfer nodes to', 'json', False,
                      default=[]),
                ],
            }
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
        raise MilvusClientError(
            f'Milvus has no typed form for {kind}.{operation}'
        )

    def visual_admin_catalog(self, catalog):
        catalog['native_planner'] = 'pymilvus-structured-planner'
        catalog['query_language'] = 'Milvus query/search JSON'
        catalog['consistency_authority'] = 'milvus'
        catalog['common_finality_interpretation'] = False
        catalog['experience_families'] = ['vector']

        def declaration(resource_kinds, operation_kinds, reason, evidence):
            return {
                'status': 'supported', 'resource_kinds': resource_kinds,
                'operation_obligations': {
                    kind: sorted(self.ADMIN_OPERATIONS[kind])
                    for kind in operation_kinds
                },
                'reason': reason, 'evidence': [evidence],
            }

        catalog['concept_declarations'] = {'vector': {
            'collections': declaration(
                ['collection'], ('collection',),
                'Collections have native schema, property, entity-grid and '
                'lifecycle editors backed by PyMilvus 2.6.5.',
                'milvus-2.6.5-vector-collections',
            ),
            'fields': declaration(
                ['field'], ('field',),
                'Fields are discovered from collection schemas and support '
                'native dynamic-field addition and property alteration.',
                'milvus-2.6.5-vector-fields',
            ),
            'indexes': declaration(
                ['vector-index'], ('vector-index',),
                'Vector indexes use algorithm-aware typed build controls and '
                'native index-property lifecycle operations.',
                'milvus-2.6.5-vector-indexes',
            ),
            'partitions': declaration(
                ['partition'], ('partition',),
                'Partitions expose native statistics, entity-grid mutations '
                'and lifecycle operations.',
                'milvus-2.6.5-vector-partitions',
            ),
            'load_state': declaration(
                ['load-state'], ('load-state',),
                'Collection and partition load state is queried and changed '
                'through explicit load/release controls.',
                'milvus-2.6.5-load-state',
            ),
            'resource_groups': declaration(
                ['resource-group'], ('resource-group',),
                'Resource groups expose live capacity, nodes, replicas and '
                'typed request/limit/transfer policy administration.',
                'milvus-2.6.5-resource-groups',
            ),
        }}
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
            target = self._native_target(request.get('target_resource'))
            name = (
                target.get('name') or target.get('collection_name') or
                target.get('field_name') or target.get('index_name') or
                draft.get('name')
            )
            if kind == 'database' and operation in {'create', 'alter'}:
                if operation == 'create':
                    _name(name, 'database')
                changes, clear = self._database_property_changes(draft)
                if operation == 'create' and clear:
                    raise MilvusClientError(
                        'database creation cannot clear properties'
                    )
                if operation == 'alter' and not changes and not clear:
                    raise MilvusClientError(
                        'database alteration requires a property change'
                    )
            if kind == 'resource-group':
                if operation == 'create':
                    _name(name, 'resource group')
                elif operation == 'alter':
                    _name(name, 'resource group')
                    self._resource_group_config(draft)
            if kind == 'collection' and operation == 'create':
                _name(name, 'collection')
                schema = draft.get('schema')
                if schema is None:
                    _integer(
                        draft.get('dimension'), 'dimension', 128, 1, 65535
                    )
                    primary = _name(
                        draft.get('primary_field_name', 'id'), 'primary field'
                    )
                    vector = _name(
                        draft.get('vector_field_name', 'vector'),
                        'vector field',
                    )
                    if primary == vector:
                        raise MilvusClientError(
                            'primary and vector fields must be distinct'
                        )
                    if draft.get('metric_type', 'COSINE') not in {
                        'COSINE', 'L2', 'IP'
                    }:
                        raise MilvusClientError('metric type is invalid')
                    id_type = draft.get('primary_id_type', 'int')
                    if id_type not in {'int', 'string'}:
                        raise MilvusClientError(
                            'primary ID type is invalid'
                        )
                    if id_type == 'string':
                        _integer(
                            draft.get('primary_max_length'),
                            'maximum primary string length', 65535, 1, 65535,
                        )
                else:
                    self._validate_schema(schema)
            if kind == 'collection' and operation == 'alter':
                self._collection_property_changes(draft)
            if kind == 'collection' and operation == 'rename':
                _name(draft.get('new_name'), 'new collection name')
            if kind in {'collection', 'partition'} and operation in {
                'insert', 'update', 'delete'
            }:
                _name(
                    draft.get('collection_name') or
                    target.get('collection_name'), 'collection',
                )
                if draft.get('partition_name'):
                    _name(draft['partition_name'], 'partition')
                if operation in {'insert', 'update'}:
                    if not isinstance(draft.get('data'), list) or not draft[
                        'data'
                    ] or not all(isinstance(item, Mapping)
                                 for item in draft['data']):
                        raise MilvusClientError(
                            'entity data must be a non-empty object array'
                        )
                elif not draft.get('filter') and draft.get('ids') is None:
                    raise MilvusClientError(
                        'delete requires a filter or primary IDs'
                    )
            if kind == 'field' and operation == 'create':
                _name(
                    draft.get('collection_name') or
                    target.get('collection_name'), 'collection',
                )
                _name(name, 'field')
                data_type = draft.get('data_type')
                if data_type not in _FIELD_TYPES:
                    raise MilvusClientError('field data type is invalid')
                self._field_type_options(draft, data_type)
            if kind == 'field' and operation == 'alter':
                field_mode = _property_mode(
                    draft.get('mmap_mode'), 'mmap'
                )
                if field_mode not in {'enabled', 'disabled'}:
                    raise MilvusClientError(
                        'field alteration requires a property change'
                    )
            if kind == 'vector-index' and operation == 'create':
                _name(draft.get('collection_name'), 'collection')
                _name(draft.get('field_name'), 'field')
                _name(draft.get('index_name'), 'index')
                self._index_parameters(draft)
            if kind == 'vector-index' and operation == 'alter':
                if _property_mode(
                    draft.get('mmap_mode'), 'mmap'
                ) == 'unchanged':
                    raise MilvusClientError(
                        'index alteration requires a property change'
                    )
            if kind == 'partition' and operation == 'create':
                _name(draft.get('collection_name'), 'collection')
                _name(name, 'partition')
            if kind == 'alias' and operation in {'create', 'alter'}:
                _name(draft.get('collection_name'), 'collection')
                _name(name, 'alias')
            if kind == 'load-state' and operation == 'execute':
                if draft.get('action') not in {'load', 'release'}:
                    raise MilvusClientError('load-state action is invalid')
                if draft.get('action') == 'load':
                    _integer(
                        draft.get('replica_number'), 'replica number',
                        1, 1, 1024,
                    )
                    _string_list(
                        draft.get('resource_groups', []), 'resource groups'
                    )
                    _string_list(
                        draft.get('load_fields', []), 'load fields'
                    )
            if kind == 'compaction' and operation == 'execute':
                _name(draft.get('collection_name'), 'collection')
                if draft.get('compaction_kind', 'merge') not in {
                    'merge', 'clustering', 'level-zero'
                }:
                    raise MilvusClientError('compaction kind is invalid')
            if kind in {'user', 'credential'} and operation == 'create':
                _name(name, 'user')
                _text(
                    draft.get('password_reference'),
                    'password secret reference',
                )
            if kind in {'user', 'credential'} and operation == 'alter':
                _name(name, 'user')
                _text(
                    draft.get('current_password_reference'),
                    'current password secret reference',
                )
                _text(
                    draft.get('new_password_reference'),
                    'new password secret reference',
                )
            if kind == 'role' and operation == 'create':
                _name(name, 'role')
            if kind == 'user' and operation in {'grant', 'revoke'}:
                _name(draft.get('user_name') or name, 'user')
                _name(draft.get('role_name'), 'role')
            if kind in {'role', 'privilege'} and operation in {
                'grant', 'revoke'
            }:
                _name(draft.get('role_name'), 'role')
                _text(draft.get('object_type'), 'object type')
                _text(draft.get('object_name'), 'object name')
                _text(draft.get('privilege'), 'privilege')
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
            return self._inspect_admin(client, route, kind, target)
        if route['read_only']:
            raise MilvusClientError('read-only route refused mutation')
        if kind == 'database':
            changes, clear = self._database_property_changes(draft)
            if operation == 'create':
                return client.create_database(
                    db_name=_name(name),
                    properties=changes,
                )
            if operation == 'alter':
                if clear:
                    return client.drop_database_properties(
                        db_name=_name(name), property_keys=clear,
                    )
                return client.alter_database_properties(
                    db_name=_name(name), properties=changes,
                )
            return client.drop_database(db_name=_name(name))
        if kind == 'resource-group':
            if operation == 'create':
                return client.create_resource_group(
                    name=_name(name, 'resource group')
                )
            if operation == 'alter':
                return client.update_resource_groups(
                    configs={
                        _name(name, 'resource group'):
                        self._resource_group_config(draft)
                    }
                )
            return client.drop_resource_group(
                name=_name(name, 'resource group')
            )
        database = (
            target.get('database') or draft.get('database') or
            route['database']
        )
        if kind in {
            'collection', 'field', 'partition', 'vector-index', 'alias',
            'load-state', 'compaction',
        }:
            self._invoke(client, 'use_database', db_name=_name(database))
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
                        'id_type': draft.get('primary_id_type', 'int'),
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
                    if arguments['id_type'] == 'string':
                        arguments['max_length'] = _integer(
                            draft.get('primary_max_length'),
                            'maximum primary string length', 65535, 1, 65535,
                        )
                return client.create_collection(**arguments)
            if operation == 'alter':
                changes, clear = self._collection_property_changes(draft)
                if clear:
                    return client.drop_collection_properties(
                        collection_name=collection, property_keys=clear,
                    )
                return client.alter_collection_properties(
                    collection_name=collection, properties=changes,
                )
            if operation == 'rename':
                return client.rename_collection(
                    old_name=collection,
                    new_name=_name(draft['new_name'], 'new collection name'),
                )
            if operation == 'drop':
                return client.drop_collection(collection_name=collection)
        if kind == 'field':
            collection = _name(
                draft.get('collection_name') or
                target.get('collection_name'), 'collection',
            )
            field = _name(name, 'field')
            if operation == 'create':
                data_type = draft['data_type']
                return client.add_collection_field(
                    collection_name=collection, field_name=field,
                    data_type=self._data_type(data_type),
                    **self._field_type_options(draft, data_type),
                )
            mode = _property_mode(draft.get('mmap_mode'), 'mmap')
            return client.alter_collection_field(
                collection_name=collection, field_name=field,
                field_params={'mmap.enabled': mode == 'enabled'},
            )
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
            if operation == 'drop':
                return client.drop_index(
                    collection_name=collection, index_name=index_name
                )
            if operation == 'alter':
                mode = _property_mode(draft.get('mmap_mode'), 'mmap')
                if mode == 'clear':
                    return client.drop_index_properties(
                        collection_name=collection, index_name=index_name,
                        property_keys=['mmap.enabled'],
                    )
                return client.alter_index_properties(
                    collection_name=collection, index_name=index_name,
                    properties={'mmap.enabled': mode == 'enabled'},
                )
            index_type, metric_type, index_parameters = \
                self._index_parameters(draft)
            params = client.prepare_index_params()
            params.add_index(
                field_name=_name(draft['field_name'], 'field'),
                index_name=index_name, index_type=index_type,
                metric_type=metric_type, params=index_parameters,
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
                groups = _string_list(
                    draft.get('resource_groups', []), 'resource groups'
                )
                fields = _string_list(
                    draft.get('load_fields', []), 'load fields'
                )
                arguments = {
                    'collection_name': collection,
                    'replica_number': _integer(
                        draft.get('replica_number'), 'replica count',
                        1, 1, 1024,
                    ),
                    'skip_load_dynamic_field': bool(
                        draft.get('skip_dynamic_field')
                    ),
                    'refresh': bool(draft.get('refresh')),
                }
                if groups:
                    arguments['resource_groups'] = groups
                if fields:
                    arguments['load_fields'] = fields
                return client.load_collection(
                    **arguments
                )
            return client.release_collection(collection_name=collection)
        if kind == 'compaction' and operation == 'execute':
            compaction_kind = draft.get('compaction_kind', 'merge')
            return client.compact(
                collection_name=_name(
                    draft['collection_name'], 'collection'
                ),
                is_clustering=compaction_kind == 'clustering',
                is_l0=compaction_kind == 'level-zero',
            )
        if kind in {'user', 'credential'} and operation in {'create', 'alter'}:
            user = _name(name, 'user')
            if operation == 'create':
                return self._with_admin_secrets(
                    route, {'password': draft['password_reference']},
                    lambda secrets: client.create_user(
                        user_name=user, password=secrets['password']
                    ),
                )
            return self._with_admin_secrets(
                route, {
                    'current': draft['current_password_reference'],
                    'new': draft['new_password_reference'],
                },
                lambda secrets: client.update_password(
                    user_name=user, old_password=secrets['current'],
                    new_password=secrets['new'],
                ),
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

    def _inspect_admin(self, client, route, kind, target):
        name = (
            target.get('name') or target.get('collection_name') or
            target.get('field_name') or target.get('index_name')
        )
        database = target.get('database') or route['database']
        if kind == 'cluster':
            return {
                'version': self._invoke(client, 'get_server_version'),
                'databases': self._invoke(client, 'list_databases'),
                'resource_groups': self._invoke(
                    client, 'list_resource_groups'
                ),
            }
        if kind == 'database':
            return self._invoke(
                client, 'describe_database', db_name=_name(name, 'database')
            )
        if kind == 'resource-group':
            return self._json_value(self._invoke(
                client, 'describe_resource_group',
                name=_name(name, 'resource group'),
            ))
        if kind in {
            'collection', 'field', 'partition', 'vector-index', 'alias',
            'load-state', 'compaction',
        }:
            self._invoke(client, 'use_database', db_name=_name(database))
        collection = target.get('collection_name')
        if kind == 'collection':
            collection = _name(name, 'collection')
            return {
                'definition': self._invoke(
                    client, 'describe_collection',
                    collection_name=collection,
                ),
                'statistics': self._invoke(
                    client, 'get_collection_stats',
                    collection_name=collection,
                ),
                'load_state': self._invoke(
                    client, 'get_load_state',
                    collection_name=collection,
                ),
                'aliases': self._invoke(
                    client, 'list_aliases', collection_name=collection,
                ),
            }
        if kind == 'field':
            definition = self._invoke(
                client, 'describe_collection',
                collection_name=_name(collection, 'collection'),
            )
            field_name = _name(name, 'field')
            field = next((
                item for item in definition.get('fields', [])
                if (item.get('name') or item.get('field_name')) == field_name
            ), None)
            if field is None:
                raise MilvusClientError('Milvus field is unavailable')
            return field
        if kind == 'partition':
            partition = _name(name, 'partition')
            collection = _name(collection, 'collection')
            return {
                'exists': self._invoke(
                    client, 'has_partition', collection_name=collection,
                    partition_name=partition,
                ),
                'statistics': self._invoke(
                    client, 'get_partition_stats',
                    collection_name=collection, partition_name=partition,
                ),
                'load_state': self._invoke(
                    client, 'get_load_state', collection_name=collection,
                    partition_name=partition,
                ),
            }
        if kind == 'vector-index':
            return self._invoke(
                client, 'describe_index',
                collection_name=_name(collection, 'collection'),
                index_name=_name(name, 'index'),
            )
        if kind == 'alias':
            return self._invoke(
                client, 'describe_alias', alias=_name(name, 'alias')
            )
        if kind == 'load-state':
            return self._invoke(
                client, 'get_load_state',
                collection_name=_name(collection or name, 'collection'),
            )
        if kind == 'compaction':
            job_id = target.get('job_id')
            if job_id is None:
                return {
                    'collection_name': collection,
                    'job_id_required_for_state': True,
                }
            job_id = _integer(
                job_id, 'compaction job ID', 0, 0, 2**63 - 1
            )
            return {
                'job_id': job_id,
                'state': self._invoke(
                    client, 'get_compaction_state', job_id=job_id
                ),
            }
        if kind in {'user', 'credential'}:
            return self._invoke(
                client, 'describe_user', user_name=_name(name, 'user')
            )
        if kind in {'role', 'privilege'}:
            role = target.get('role_name') or name
            return self._invoke(
                client, 'describe_role', role_name=_name(role, 'role')
            )
        raise MilvusClientError('resource inspection is unavailable')

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
            arguments = {
                'collection_name': _name(collection, 'collection'),
                'filter': filter_value, 'output_fields': ['*'],
                'limit': limit, 'offset': offset,
                'timeout': route['operation_timeout'],
                'consistency_level': route['consistency_level'],
            }
            resource = request.get('target_resource') or {}
            if resource.get('resource_kind') == 'partition':
                partition = target.get('partition_name') or target.get('name')
                arguments['partition_names'] = [
                    _name(partition, 'partition')
                ]
            result = client.query(
                **arguments
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
        if isinstance(value, Mapping):
            unknown_schema = sorted(set(value).difference({
                'fields', 'auto_id', 'enable_dynamic_field',
            }))
            if unknown_schema:
                raise MilvusClientError(
                    'advanced schema contains unknown properties: '
                    f'{unknown_schema}'
                )
            for option in ('auto_id', 'enable_dynamic_field'):
                if option in value and not isinstance(value[option], bool):
                    raise MilvusClientError(
                        f'advanced schema {option} must be boolean'
                    )
        admitted = {
            'name', 'datatype', 'is_primary', 'dim',
            'max_length', 'nullable', 'default_value', 'element_type',
            'max_capacity', 'is_partition_key', 'is_clustering_key',
        }
        names = set()
        primary_fields = []
        vector_fields = []
        partition_keys = []
        clustering_keys = []
        for field in cls._schema_fields(value):
            field = _mapping(field, 'schema field')
            unknown = sorted(set(field).difference(admitted))
            if unknown:
                raise MilvusClientError(
                    f'schema field contains unknown properties: {unknown}'
                )
            name = _name(field.get('name'), 'schema field')
            if name in names:
                raise MilvusClientError(
                    f'advanced schema field {name!r} is duplicated'
                )
            names.add(name)
            datatype = _text(
                field.get('datatype'), 'schema field datatype', 64
            ).upper()
            if datatype not in _FIELD_TYPES:
                raise MilvusClientError(
                    f'Milvus datatype {datatype!r} is unavailable'
                )
            for option in (
                'is_primary', 'nullable', 'is_partition_key',
                'is_clustering_key',
            ):
                if option in field and not isinstance(field[option], bool):
                    raise MilvusClientError(
                        f'schema field {option} must be boolean'
                    )
            if field.get('is_primary'):
                primary_fields.append((name, datatype, field))
            if 'VECTOR' in datatype:
                vector_fields.append(name)
                if datatype != 'SPARSE_FLOAT_VECTOR':
                    dimension = _integer(
                        field.get('dim'), f'{name} vector dimension',
                        0, 1, 65535,
                    )
                    if datatype == 'BINARY_VECTOR' and dimension % 8:
                        raise MilvusClientError(
                            'binary vector dimension must be divisible by 8'
                        )
                elif 'dim' in field:
                    raise MilvusClientError(
                        'sparse vector fields do not accept a dimension'
                    )
            elif 'dim' in field:
                raise MilvusClientError(
                    f'non-vector field {name!r} cannot define a dimension'
                )
            if datatype == 'VARCHAR':
                _integer(
                    field.get('max_length'), f'{name} maximum length',
                    0, 1, 65535,
                )
            elif 'max_length' in field:
                raise MilvusClientError(
                    f'non-string field {name!r} cannot define max_length'
                )
            if datatype == 'ARRAY':
                element = field.get('element_type')
                if element not in _FIELD_TYPES or element == 'ARRAY' or (
                    'VECTOR' in str(element)
                ):
                    raise MilvusClientError(
                        f'array field {name!r} has an invalid element type'
                    )
                _integer(
                    field.get('max_capacity'), f'{name} maximum capacity',
                    4096, 1, 4096,
                )
            elif {'element_type', 'max_capacity'}.intersection(field):
                raise MilvusClientError(
                    f'non-array field {name!r} has array-only properties'
                )
            if field.get('is_partition_key'):
                partition_keys.append(name)
            if field.get('is_clustering_key'):
                clustering_keys.append(name)
        if len(primary_fields) != 1:
            raise MilvusClientError(
                'advanced schema requires exactly one primary field'
            )
        primary_name, primary_type, primary = primary_fields[0]
        if primary_type not in {'INT64', 'VARCHAR'}:
            raise MilvusClientError(
                'primary field must use INT64 or VARCHAR'
            )
        if primary.get('nullable') or 'default_value' in primary:
            raise MilvusClientError(
                f'primary field {primary_name!r} cannot be nullable or '
                'define a default'
            )
        if not vector_fields:
            raise MilvusClientError(
                'advanced vector schema requires at least one vector field'
            )
        if len(partition_keys) > 1:
            raise MilvusClientError(
                'advanced schema permits at most one partition key'
            )
        if len(clustering_keys) > 1:
            raise MilvusClientError(
                'advanced schema permits at most one clustering key'
            )

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

    def _with_admin_secrets(self, route, references, callback):
        if not callable(self.secret_acquirer):
            raise MilvusClientError('secret acquisition is unavailable')
        principal = route.get('principal_reference')
        if not principal:
            raise MilvusClientError(
                'administrative secrets require a principal reference'
            )
        bindings = [
            (label, _text(reference, f'{label} secret reference'))
            for label, reference in references.items()
        ]

        def acquire(index, values):
            if index == len(bindings):
                return callback(values)
            label, reference = bindings[index]
            lease = self.secret_acquirer(
                reference, principal, 'administer', 'database_password'
            )
            with lease:
                return lease.use(lambda view: acquire(index + 1, {
                    **values,
                    label: _text(
                        bytes(view).decode('utf-8'), 'resolved password',
                        65536,
                    ),
                }))

        return acquire(0, {})

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
