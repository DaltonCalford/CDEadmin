##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""YugabyteDB YCQL adapter over the qualified CQL driver boundary."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from urllib.request import Request, urlopen

from ..cql_native import CassandraClient, CassandraClientError


REFERENCE_VERSION = '2025.2.2.2'
YCQL_PROTOCOL_VERSION = 4


class YugabyteDBYCQLClientError(CassandraClientError):
    """A YCQL request could not be handled safely."""


class YugabyteDBYCQLClient(CassandraClient):
    """Bounded YCQL adapter without Cassandra-only feature advertising."""

    DEFAULT_CLUSTER_NAME = 'YugabyteDB'
    ROUTE_KEYS = CassandraClient.ROUTE_KEYS | frozenset({
        'version_api_scheme', 'version_api_host', 'version_api_port',
    })
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
        ('user-defined-type', 'SELECT * FROM system_schema.types'),
        ('role', (
            'SELECT role, can_login, is_superuser, member_of '
            'FROM system_auth.roles'
        )),
        ('permission', (
            'SELECT role, resource, permissions '
            'FROM system_auth.role_permissions'
        )),
    )
    VIRTUAL_RESOURCES = (('query', 'YCQL query execution'),)
    DISCOVERY_NAME_FIELDS = {
        'keyspace': 'keyspace_name', 'table': 'table_name',
        'column': 'column_name', 'index': 'index_name',
        'user-defined-type': 'type_name', 'role': 'role',
        'permission': 'role',
    }
    ADMIN_OPERATIONS = {
        'cluster': frozenset({'inspect'}),
        'datacenter': frozenset({'inspect'}),
        'node': frozenset({'inspect'}),
        'keyspace': frozenset({'inspect', 'create', 'alter', 'drop'}),
        'table': frozenset({
            'inspect', 'create', 'alter', 'insert', 'update', 'delete',
            'drop',
        }),
        'column': frozenset({
            'inspect', 'create', 'alter', 'rename', 'drop',
        }),
        'index': frozenset({'inspect', 'create', 'drop'}),
        'user-defined-type': frozenset({
            'inspect', 'create', 'alter', 'drop',
        }),
        'role': frozenset({
            'inspect', 'create', 'alter', 'grant', 'revoke', 'drop',
        }),
        'permission': frozenset({'inspect', 'grant', 'revoke'}),
        'query': frozenset({'inspect'}),
    }
    TOOL_KINDS = frozenset()
    RESOURCE_KINDS = frozenset(ADMIN_OPERATIONS)
    YCQL_PERMISSIONS = frozenset({
        'ALL', 'ALTER', 'AUTHORIZE', 'CREATE', 'DESCRIBE', 'DROP',
        'EXECUTE', 'MODIFY', 'SELECT',
    })

    def __init__(self, secret_acquirer=None, module=None,
                 version_urlopen=None):
        super().__init__(secret_acquirer, module)
        self._version_urlopen = version_urlopen or urlopen

    @classmethod
    def _route(cls, request):
        if not isinstance(request, Mapping):
            raise YugabyteDBYCQLClientError(
                'YugabyteDB YCQL request must be an object'
            )
        key = 'route' if 'route' in request else '_provider_route'
        source = request.get(key)
        if not isinstance(source, Mapping):
            raise YugabyteDBYCQLClientError(
                'YugabyteDB YCQL route must be an object'
            )
        route = copy.deepcopy(dict(source))
        requested = route.get('protocol_version', YCQL_PROTOCOL_VERSION)
        if isinstance(requested, bool):
            requested = None
        try:
            valid_protocol = int(requested) == YCQL_PROTOCOL_VERSION
        except (TypeError, ValueError):
            valid_protocol = False
        if not valid_protocol:
            raise YugabyteDBYCQLClientError(
                'YugabyteDB YCQL requires native protocol version 4'
            )
        # Reuse the hardened route parser, whose Cassandra profile is pinned
        # to v5, and then restore the YCQL-owned protocol decision.
        route['protocol_version'] = 5
        try:
            normalized = super()._route({key: route})
        except CassandraClientError as exc:
            raise YugabyteDBYCQLClientError(
                str(exc).replace('Cassandra', 'YugabyteDB YCQL')
            ) from None
        normalized['protocol_version'] = YCQL_PROTOCOL_VERSION
        scheme = str(route.get('version_api_scheme', 'http')).lower()
        if scheme not in {'http', 'https'}:
            raise YugabyteDBYCQLClientError(
                'YugabyteDB version API scheme must be http or https'
            )
        normalized['version_api_scheme'] = scheme
        api_host = str(
            route.get('version_api_host') or normalized['host']
        )
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', api_host):
            raise YugabyteDBYCQLClientError(
                'YugabyteDB version API host is invalid'
            )
        normalized['version_api_host'] = api_host
        api_port = route.get('version_api_port', 7000)
        if isinstance(api_port, bool):
            api_port = None
        try:
            api_port = int(api_port)
        except (TypeError, ValueError):
            api_port = 0
        if not 1 <= api_port <= 65535:
            raise YugabyteDBYCQLClientError(
                'YugabyteDB version API port is invalid'
            )
        normalized['version_api_port'] = api_port
        return normalized

    def _runtime_version(self, route):
        address = (
            f'{route["version_api_scheme"]}://'
            f'{route["version_api_host"]}:{route["version_api_port"]}'
            '/api/v1/version'
        )
        request = Request(
            address,
            headers={'Accept': 'application/json'},
            method='GET',
        )
        options = {'timeout': float(route['connect_timeout'])}
        if route['version_api_scheme'] == 'https':
            options['context'] = self._ssl_context(route)
        try:
            with self._version_urlopen(request, **options) as response:
                payload = response.read(65537)
            if len(payload) > 65536:
                raise YugabyteDBYCQLClientError(
                    'YugabyteDB version response exceeds the safety limit'
                )
            value = json.loads(payload.decode('utf-8'))
        except YugabyteDBYCQLClientError:
            raise
        except Exception as exc:
            raise YugabyteDBYCQLClientError(
                'YugabyteDB version API failed '
                f'({type(exc).__name__})'
            ) from None
        if not isinstance(value, Mapping):
            raise YugabyteDBYCQLClientError(
                'YugabyteDB version API returned an invalid document'
            )
        version = str(value.get('version_number') or '')
        if version != REFERENCE_VERSION:
            raise YugabyteDBYCQLClientError(
                'YugabyteDB version API is outside the 2025.2.2.2 profile'
            )
        return copy.deepcopy(dict(value))

    def runtime_identity(self, request, handle=None):
        owned = handle is None
        cluster = session = None
        try:
            if handle is None:
                cluster, session, route = self._connect(request)
            else:
                cluster, session = handle.cluster, handle.session
                route = handle.route
            rows = self._rows(session, (
                'SELECT cluster_name, release_version, cql_version, '
                'native_protocol_version, data_center, rack, partitioner, '
                'host_id FROM system.local'
            ))
            row = rows[0] if rows else {}
            cql_release = str(row.get('release_version') or '')
            if cql_release != '3.9-SNAPSHOT':
                raise YugabyteDBYCQLClientError(
                    'endpoint does not expose the YugabyteDB YCQL signature'
                )
            native_protocol = str(
                row.get('native_protocol_version') or ''
            )
            if native_protocol and native_protocol != '4':
                raise YugabyteDBYCQLClientError(
                    'YugabyteDB YCQL runtime did not negotiate protocol v4'
                )
            if str(row.get('cql_version') or '') != '3.4.2':
                raise YugabyteDBYCQLClientError(
                    'endpoint does not expose the YugabyteDB YCQL CQL profile'
                )
            build = self._runtime_version(route)
            return {
                'engine_id': 'yugabytedb',
                'version': REFERENCE_VERSION,
                'build_id': (
                    f'{build.get("git_hash", "unknown")}:'
                    f'{build.get("build_number", "unknown")}'
                ),
                'protocol_id': 'cql',
                'native': {
                    'api': 'ycql',
                    'ycql_compatibility_release': cql_release,
                    'cluster_name': row.get('cluster_name'),
                    'cql_version': row.get('cql_version'),
                    'native_protocol_version': native_protocol or '4',
                    'data_center': row.get('data_center'),
                    'rack': row.get('rack'),
                    'host_id': row.get('host_id'),
                    'driver_version': str(self.module.__version__),
                    'server_build_number': build.get('build_number'),
                    'server_git_hash': build.get('git_hash'),
                    'server_build_type': build.get('build_type'),
                },
            }
        except YugabyteDBYCQLClientError:
            raise
        except Exception as exc:
            raise YugabyteDBYCQLClientError(
                'YugabyteDB YCQL runtime identity failed '
                f'({type(exc).__name__})'
            ) from None
        finally:
            if owned and cluster is not None:
                self._close_native(cluster, session)

    @staticmethod
    def describe_transaction(handle):
        return {
            'native_boundary': 'yugabytedb-ycql-operation',
            'keyspace': handle.keyspace,
            'distributed_transactions_are_table_configured': True,
            'batch_and_transaction_outcomes': (
                'yugabytedb-driver-and-server-owned'
            ),
            'consistency_outcome': 'yugabytedb-driver-and-server-owned',
            'common_finality_inference': False,
            'retry_decision_owned_by_common_code': False,
        }

    @staticmethod
    def _resource(kind, name, native, parent=None):
        item = CassandraClient._resource(kind, name, native, parent)
        item['resource_id'] = item['resource_id'].replace(
            'cassandra:', 'yugabytedb:ycql:', 1
        )
        if kind == 'permission':
            item['resource_id'] += ':' + item['generation']
        for field in ('display_path', 'authority_path'):
            path = item[field]
            item[field] = ['yugabytedb', 'ycql', *path[1:]]
        return item

    def list_resources(self, request):
        resources = super().list_resources(request)
        return [
            item for item in resources
            if item['resource_kind'] in self.RESOURCE_KINDS
        ]

    def inspect_resource(self, request):
        resource_id = request.get('resource_id')
        for resource in self.list_resources(request):
            if resource['resource_id'] == resource_id:
                return resource
        raise YugabyteDBYCQLClientError(
            'YugabyteDB YCQL resource is unavailable'
        )

    def describe_security(self, request):
        try:
            roles = self._read(request, (
                'SELECT role, can_login, is_superuser, member_of '
                'FROM system_auth.roles'
            ))
        except Exception:
            roles = []
        try:
            permissions = self._read(request, (
                'SELECT role, resource, permissions '
                'FROM system_auth.role_permissions'
            ))
        except Exception:
            permissions = []
        generation = hashlib.sha256(json.dumps(
            [roles, permissions], sort_keys=True, default=str
        ).encode('utf-8')).hexdigest()[:20]
        return {
            'resource_id': 'yugabytedb:ycql:security:current',
            'display_name': 'YugabyteDB YCQL roles and permissions',
            'authority_path': [
                'yugabytedb', 'ycql', 'security', 'current',
            ],
            'generation': generation,
            'native': {
                'authorization_model': 'yugabytedb-ycql-native-rbac',
                'roles': roles,
                'permissions': permissions,
            },
        }

    def visual_admin_catalog(self, catalog):
        result = super().visual_admin_catalog(catalog)
        result['objects'] = [
            item for item in result.get('objects', [])
            if item['resource_kind'] in self.RESOURCE_KINDS
        ]
        result['native_planner'] = 'yugabytedb-ycql-structured-planner'
        result['query_language'] = 'YCQL'
        result['transaction_authority'] = 'yugabytedb-and-ycql-driver'
        for resource in result.get('objects', []):
            for operation in resource.get('operations', []):
                form = operation.get('form')
                if isinstance(form, dict):
                    form['form_id'] = form['form_id'].replace(
                        'cassandra-', 'yugabytedb-ycql-'
                    )
                    form['title'] = form['title'].replace('Cassandra', 'YCQL')
                if (
                    resource['resource_kind'] == 'table' and
                    operation['operation_id'] == 'create'
                ):
                    operation['form'] = self._ycql_table_form()
                if (
                    resource['resource_kind'] == 'index' and
                    operation['operation_id'] == 'create'
                ):
                    operation['form'] = self._ycql_index_form()
                if (
                    resource['resource_kind'] == 'role' and
                    operation['operation_id'] in {'create', 'alter'}
                ):
                    operation['form'] = self._ycql_role_form(
                        operation['operation_id']
                    )
        return result

    def _ycql_table_form(self):
        f = self._field
        return self._form(
            'yugabytedb-ycql-table-create', 'Create YCQL table', [
                f('keyspace', 'Keyspace', required=True),
                f('name', 'Table name', required=True),
                f('columns', 'Columns', 'json', True, default=[]),
                f('partition_keys', 'Partition key columns', 'json', True,
                  default=[]),
                f('clustering_keys', 'Clustering columns', 'json', False,
                  default=[]),
                f('tablets', 'Initial tablet count', 'number', False,
                  minimum=1, maximum=100000),
                f('transactions_enabled', 'Distributed transactions',
                  'boolean', False, default=False),
                f('transaction_consistency', 'Transaction consistency',
                  'select', False, default='strong', options=[
                      {'value': 'strong', 'label': 'Strong'},
                      {'value': 'user_enforced', 'label': 'User enforced'},
                  ]),
            ],
        )

    def _ycql_index_form(self):
        f = self._field
        return self._form(
            'yugabytedb-ycql-index-create', 'Create YCQL index', [
                f('keyspace', 'Keyspace', required=True),
                f('table', 'Table', required=True),
                f('name', 'Index name', required=True),
                f('target', 'Column or collection target', required=True),
            ],
        )

    def _ycql_role_form(self, operation):
        f = self._field
        fields = [] if operation == 'alter' else [
            f('name', 'Role name', required=True)
        ]
        fields.extend([
            f('login', 'Can log in', 'boolean', False, default=False),
            f('superuser', 'Superuser', 'boolean', False, default=False),
            f('password_credential_reference',
              'Password credential reference', 'secret-reference', False,
              sensitive=True),
        ])
        return self._form(
            f'yugabytedb-ycql-role-{operation}',
            f'{operation.title()} YCQL role', fields,
        )

    def validate_admin_operation(self, request):
        validation = super().validate_admin_operation(request)
        errors = validation['errors']
        draft = request.get('draft', {})
        if (
            request.get('resource_kind') == 'table' and
            request.get('operation_id') == 'create'
        ):
            tablets = draft.get('tablets')
            if (
                tablets is not None and
                (isinstance(tablets, bool) or not isinstance(tablets, int) or
                 not 1 <= tablets <= 100000)
            ):
                errors.append({
                    'field_id': 'tablets', 'code': 'ycql_validation',
                    'message': (
                        'tablet count must be an integer from 1 to 100000'
                    ),
                })
            consistency = draft.get('transaction_consistency', 'strong')
            if consistency not in {'strong', 'user_enforced'}:
                errors.append({
                    'field_id': 'transaction_consistency',
                    'code': 'ycql_validation',
                    'message': 'transaction consistency is invalid',
                })
            transactions = draft.get('transactions_enabled', False)
            if not isinstance(transactions, bool):
                errors.append({
                    'field_id': 'transactions_enabled',
                    'code': 'ycql_validation',
                    'message': 'distributed transactions must be boolean',
                })
        return validation

    def plan_admin_operation(self, request):
        payload = copy.deepcopy(request)
        validation = self.validate_admin_operation(payload)
        if validation['errors']:
            raise YugabyteDBYCQLClientError(
                validation['errors'][0]['message']
            )
        kind = payload.get('resource_kind')
        operation = payload.get('operation_id')
        draft = payload.setdefault('draft', {})
        if kind == 'table' and operation == 'create':
            options = {}
            tablets = draft.pop('tablets', None)
            if tablets is not None:
                options['tablets'] = tablets
            transactions = bool(draft.pop('transactions_enabled', False))
            consistency = draft.pop('transaction_consistency', 'strong')
            if transactions or consistency == 'user_enforced':
                transaction_options = {'enabled': transactions}
                if consistency == 'user_enforced':
                    transaction_options['consistency_level'] = consistency
                options['transactions'] = transaction_options
            draft['options'] = options
        if kind == 'index' and operation == 'create':
            draft['index_kind'] = 'secondary'
            draft['options'] = {}
        if kind == 'role' and operation in {'create', 'alter'}:
            draft['options'] = {}
        plan = super().plan_admin_operation(payload)
        preview = plan['command_preview']
        preview['driver'] = 'apache-cassandra-python-driver-ycql-v4'
        preview['language'] = 'YCQL'
        preview['native_api'] = 'ycql'
        plan['warnings'] = [
            value.replace('Cassandra', 'YugabyteDB YCQL')
            for value in plan['warnings']
        ]
        return plan

    def _security_statements(self, kind, operation, draft, native, preview):
        if kind == 'permission':
            privileges = draft.get('privileges', [])
            if any(
                str(value).upper() not in self.YCQL_PERMISSIONS
                for value in privileges
            ):
                raise YugabyteDBYCQLClientError(
                    'YCQL permission is invalid'
                )
        return super()._security_statements(
            kind, operation, draft, native, preview
        )

    @staticmethod
    def _native_target(target):
        if not isinstance(target, Mapping):
            raise YugabyteDBYCQLClientError('target resource is invalid')
        extension = target.get('extensions', {}).get('yugabytedb', {})
        native = extension.get('native')
        if not isinstance(native, Mapping):
            native = target.get('native')
        if not isinstance(native, Mapping):
            raise YugabyteDBYCQLClientError(
                'target lacks YugabyteDB YCQL native identity'
            )
        return copy.deepcopy(dict(native))
