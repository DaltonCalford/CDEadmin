##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""PostgreSQL provider adapters around preserved pgAdmin behavior.

This module owns PostgreSQL-specific catalog templates, driver calls and
transaction presentation.  Common CDEadmin code treats every provider-native
transaction payload as opaque.
"""

from __future__ import annotations

import copy
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any, Mapping

from flask import render_template

from pgadmin.utils.compile_template_name import compile_template_path
from pgadmin.utils.driver.registry import DriverRegistry
from pgadmin.cdeadmin.data_studio import (
    CompletionContribution,
    ExecutionContribution,
    LanguageContribution,
    SessionContribution,
)
from pgadmin.cdeadmin.results import ResultAdapterContribution
from pgadmin.cdeadmin.resources.models import (
    ResourceCommandContribution,
    ResourceInspectorContribution,
)
from pgadmin.cdeadmin.visual_admin import (
    ProviderVisualAdministration,
    enrich_engine_experience,
)
from .preserved_surface import concept_declarations


PROVIDER_ID = 'org.pgadmin.postgresql'
PROVIDER_VERSION = '9.17.0'
PROFILE_ID = 'postgresql-native'
PROFILE_VERSION = '18.3'
LEGACY_DRIVER_TYPE = 'psycopg3'
APPROVED_DRIVER_VERSIONS = frozenset({'3.2.13', '3.3.4'})
EVIDENCE_REFERENCE = 'cde-prep-050:postgresql-preservation-20260831'
CATALOG_KINDS = frozenset({'database', 'schema', 'table'})
TABULAR_RENDER_CAPABILITY = 'postgresql.result.tabular.render'

TOOL_DISPOSITIONS = {
    'backup': 'provider-local-existing-tool',
    'restore': 'provider-local-existing-tool',
    'maintenance': 'provider-local-existing-tool',
    'psql': 'provider-local-existing-tool',
    'debugger': 'provider-local-existing-tool',
}

POSTGRESQL_CONCEPT_DECLARATIONS = {
    'relational': concept_declarations(),
}


class PostgreSQLProviderError(RuntimeError):
    """A PostgreSQL provider operation cannot be completed safely."""


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, 'to_dict', None)
    if callable(to_dict):
        return to_dict()
    raise PostgreSQLProviderError('provider request must be a contract DTO')


def _extension(payload: Mapping[str, Any]) -> dict[str, Any]:
    extensions = payload.get('extensions', {})
    if not isinstance(extensions, Mapping):
        raise PostgreSQLProviderError('extensions must be an object')
    value = extensions.get('postgresql', {})
    if not isinstance(value, Mapping):
        raise PostgreSQLProviderError(
            'extensions.postgresql must be an object'
        )
    return dict(value)


def _required_int(value: Mapping[str, Any], name: str) -> int:
    item = value.get(name)
    if isinstance(item, bool) or not isinstance(item, int):
        raise PostgreSQLProviderError(f'postgresql.{name} must be an integer')
    return item


@dataclass
class _SessionState:
    server_id: int
    database_id: int | None
    connection_id: str
    connection: object


@dataclass
class _OperationState:
    operation: dict[str, Any]
    execution_id: str
    session_id: str
    connection: object
    result: dict[str, Any] | None = None


class PostgreSQLCatalogSource:
    """Execute preserved PostgreSQL browser templates without moving them."""

    def __init__(self, driver, renderer=render_template):
        self._driver = driver
        self._renderer = renderer

    def read(
        self,
        catalog_kind: str,
        route: Mapping[str, Any],
        object_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if catalog_kind not in CATALOG_KINDS:
            raise PostgreSQLProviderError(
                f'unsupported PostgreSQL catalog kind {catalog_kind!r}'
            )
        server_id = _required_int(route, 'server_id')
        manager = self._driver.connection_manager(server_id)
        if manager is None:
            raise PostgreSQLProviderError('PostgreSQL server is unavailable')
        if catalog_kind == 'database':
            return self._databases(manager, object_id)
        database_id = _required_int(route, 'database_id')
        connection = manager.connection(did=database_id)
        if catalog_kind == 'schema':
            return self._schemas(manager, connection, route, object_id)
        return self._tables(manager, connection, route, object_id)

    def _databases(self, manager, object_id):
        connection = manager.connection()
        template = 'databases/sql/#{0}#/nodes.sql'.format(manager.version)
        sql = self._renderer(
            template,
            did=object_id,
            conn=connection,
            last_system_oid=0,
            show_system_objects=True,
            db_restrictions=None,
        )
        status, result = connection.execute_dict(sql)
        return self._rows(status, result)

    def _schemas(self, manager, connection, route, object_id):
        template = 'schemas/{0}/#{1}#/sql/nodes.sql'.format(
            manager.server_type, manager.version
        )
        sql = self._renderer(
            template,
            show_sysobj=bool(route.get('show_system_objects', False)),
            scid=object_id,
            schema_restrictions=None,
            conn=connection,
        )
        status, result = connection.execute_2darray(sql)
        return self._rows(status, result)

    def _tables(self, manager, connection, route, object_id):
        schema_id = _required_int(route, 'schema_id')
        template = '/'.join((
            compile_template_path('tables/sql', manager.version),
            'nodes.sql',
        ))
        sql = self._renderer(
            template,
            scid=schema_id,
            tid=object_id,
        )
        status, result = connection.execute_2darray(sql)
        return self._rows(status, result)

    @staticmethod
    def _rows(status, result):
        if not status:
            raise PostgreSQLProviderError(str(result))
        if not isinstance(result, Mapping) or not isinstance(
            result.get('rows'), list
        ):
            raise PostgreSQLProviderError(
                'PostgreSQL catalog response has no row collection'
            )
        return [dict(row) for row in result['rows']]


class PostgreSQLProvider:
    """Endpoint-isolated facade over the existing PostgreSQL implementation."""

    def __init__(self, context, permissions, driver=None, catalog_source=None):
        self.context = context
        self.permissions = permissions
        self._driver = driver or DriverRegistry.get(LEGACY_DRIVER_TYPE)
        self._catalog = catalog_source or PostgreSQLCatalogSource(self._driver)
        self._sessions: dict[str, _SessionState] = {}
        self._operations: dict[str, _OperationState] = {}
        self._completion_adapters: dict[str, object] = {}
        self._closed = False
        self._visual_admin = ProviderVisualAdministration(
            context, permissions, 'postgresql', PROFILE_VERSION
        )

    def visual_admin_descriptor(self):
        """Describe PostgreSQL objects for the common administration UI."""
        descriptor = self._visual_admin.descriptor()
        descriptor['administration_surface'] = {
            'surface_id': 'pgadmin.preserved-postgresql-administration',
            'workflow': 'legacy_preserved',
            'object_dialogs': True,
            'editable_grid': True,
            'maintenance_tools': True,
            'provider_owned': True,
            'common_provider_reimplementation_required': False,
        }
        descriptor['concept_declarations'] = copy.deepcopy(
            POSTGRESQL_CONCEPT_DECLARATIONS
        )
        return enrich_engine_experience(descriptor)

    def validate_visual_admin(self, request):
        return self._visual_admin.validate(request)

    def plan_visual_admin(self, request):
        return self._visual_admin.plan(request)

    def apply_visual_admin(self, request):
        return self._visual_admin.apply(request)

    @staticmethod
    def semantic_model_descriptor():
        """Declare PostgreSQL semantic-query compilation explicitly."""
        return {
            'provider_id': PROVIDER_ID,
            'engine_id': 'postgresql',
            'model_family': 'relational',
            'execution_available': True,
            'language_profile': 'postgresql-sql',
            'compiler_kind': 'sql',
            'time_intelligence': {
                'operations': [
                    'as_of', 'range', 'period_to_date',
                    'period_comparison',
                ],
                'periods': [
                    'day', 'week', 'month', 'quarter', 'year',
                    'fiscal_quarter', 'fiscal_year',
                ],
            },
            'analytical_windows': {'operations': [
                'running_sum', 'moving_sum', 'moving_average', 'lag',
                'delta', 'percent_change', 'rank', 'dense_rank',
            ]},
            'materialization': 'provider_planned',
            'reason': None,
        }

    @staticmethod
    def compile_semantic_query(model, query):
        """Compile through the PostgreSQL provider-owned dialect choice."""
        from pgadmin.cdeadmin.semantic_models.compiler import compile_sql
        return compile_sql(model, query, {
            'language_profile': 'postgresql-sql',
            'quote_open': '"', 'supports_rollup': True,
        })

    def describe_semantics(self, request):
        """Implement semantic discovery without replacing PostgreSQL UI."""
        _mapping(request)
        return {
            'identity': self._identity(),
            'endpoint_id': self.context.endpoint_id,
            'resource_id': 'postgresql:semantic-model-descriptor',
            'identity_kind': 'provider-native-id',
            'resource_kind': 'semantic-model-descriptor',
            'model_family': 'relational',
            'display_name': 'PostgreSQL semantic model support',
            'parent_resource_id': None,
            'display_path': ['PostgreSQL', 'Semantic models'],
            'authority_path': ['postgresql', 'semantic-model-descriptor'],
            'is_virtual': True, 'generation': PROFILE_VERSION,
            'capability_ids': ['postgresql.semantic-query.execute'],
            'extensions': {
                'postgresql': self.semantic_model_descriptor(),
            },
        }

    def execute_analysis(self, request):
        """Compile and execute a semantic request through PostgreSQL."""
        payload = _mapping(request)
        compiled = self.compile_semantic_query(
            payload.get('semantic_model'), payload.get('semantic_query')
        )
        payload['source'] = compiled['source']
        payload['parameters'] = compiled.get('parameters', {})
        return self.execute(payload)

    def _identity(self) -> dict[str, str]:
        return {
            'contract_version': '1.0.0',
            'provider_id': PROVIDER_ID,
            'provider_version': PROVIDER_VERSION,
            'profile_id': PROFILE_ID,
            'profile_version': PROFILE_VERSION,
            'evidence_reference': EVIDENCE_REFERENCE,
        }

    def _diagnostic(
        self, code: str, message: str, severity: str = 'info',
        retryable: bool = False, details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            'identity': self._identity(),
            'diagnostic_id': str(uuid.uuid4()),
            'severity': severity,
            'code': code,
            'message': message,
            'retryable': retryable,
            'details': dict(details or {}),
            'extensions': {'postgresql': {'provider_owned': True}},
        }

    def get_legacy_driver(self, driver_type):
        if driver_type != LEGACY_DRIVER_TYPE:
            raise PostgreSQLProviderError(
                'PostgreSQL provider only preserves the psycopg3 driver'
            )
        return self._driver

    @staticmethod
    def _inspect_catalog_resource(binding, resource):
        return binding.instance.inspect_resource(resource)

    @staticmethod
    def _invoke_catalog_inspector(binding, resource, _payload):
        return binding.instance.inspect_resource(resource)

    @classmethod
    def resource_contributions(cls):
        """Describe PostgreSQL explorer behavior without common branching."""
        resource_kinds = frozenset({'database', 'schema', 'table'})
        return {
            'inspectors': (
                ResourceInspectorContribution(
                    'postgresql.catalog.inspector',
                    resource_kinds,
                    cls._inspect_catalog_resource,
                ),
            ),
            'commands': (
                ResourceCommandContribution(
                    'postgresql.catalog.inspect',
                    'Inspect properties',
                    'postgresql.catalog.read',
                    resource_kinds,
                    mutation_class='read',
                    required_permission='data_read',
                    invoke=cls._invoke_catalog_inspector,
                ),
            ),
        }

    @staticmethod
    def _studio_open_session(binding, request):
        return binding.instance.open_session(request)

    @staticmethod
    def _studio_describe_transaction(binding, request):
        return binding.instance.describe_transaction(request)

    @staticmethod
    def _studio_control_transaction(binding, request):
        return binding.instance.control_transaction(request)

    @staticmethod
    def _studio_execute(binding, request):
        return binding.instance.execute(request)

    @staticmethod
    def _studio_poll(binding, request):
        result = binding.instance.describe_result(request)
        operation = binding.instance.get_operation(request)
        return operation, result

    @staticmethod
    def _studio_cancel(binding, request):
        return binding.instance.cancel(request)

    @staticmethod
    def _studio_complete(binding, request):
        return binding.instance.complete(request)

    @classmethod
    def data_studio_contributions(cls):
        """Expose the preserved PostgreSQL Query Tool through common ports."""
        profiles = frozenset({'postgresql-sql'})
        return {
            'languages': (
                LanguageContribution(
                    'postgresql-sql',
                    'PostgreSQL SQL',
                    'text/x-pgsql',
                    frozenset({'relational'}),
                ),
            ),
            'completions': (
                CompletionContribution(
                    'postgresql.query-tool.completion',
                    profiles,
                    cls._studio_complete,
                ),
            ),
            'sessions': (
                SessionContribution(
                    'postgresql.query-tool.session',
                    profiles,
                    cls._studio_open_session,
                    cls._studio_describe_transaction,
                    frozenset({'commit', 'rollback'}),
                    cls._studio_control_transaction,
                ),
            ),
            'executions': (
                ExecutionContribution(
                    'postgresql.query-tool.execution',
                    profiles,
                    cls._studio_execute,
                    cls._studio_poll,
                    cls._studio_cancel,
                ),
            ),
        }

    @staticmethod
    def _describe_tabular_result(_binding, result):
        extensions = result.get('extensions', {}).get('postgresql', {})
        rows = extensions.get('rows')
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise PostgreSQLProviderError(
                'PostgreSQL tabular result rows must be an array'
            )
        return {
            'descriptor_version': '1.0.0',
            'capability_id': TABULAR_RENDER_CAPABILITY,
            'records': copy.deepcopy(rows),
            'limits': {
                'max_records': 10000,
                'max_page_size': 500,
                'max_record_bytes': 256 * 1024,
                'max_descriptor_bytes': 4 * 1024 * 1024,
            },
            'sampling': {'mode': 'head', 'limit': min(
                max(len(rows), 1), 10000
            )},
            'export_policy': {
                'enabled': True,
                'formats': ['csv', 'json', 'xlsx', 'svg', 'pdf'],
                'max_records': 10000,
                'max_bytes': 8 * 1024 * 1024,
                'redact_keys': [],
            },
            'worker_policy': {
                'required': False,
                'timeout_seconds': 2.0,
            },
            'renderer_id': 'cdeadmin.result.tabular.legacy-grid',
            'component_reference': 'SchemaView/DataGridView',
        }

    @classmethod
    def result_contributions(cls):
        """Normalize PostgreSQL rows without copying the production grid."""
        return {
            'adapters': (
                ResultAdapterContribution(
                    'postgresql.result.tabular.adapter',
                    frozenset({'tabular'}),
                    TABULAR_RENDER_CAPABILITY,
                    cls._describe_tabular_result,
                ),
            ),
        }

    def query_tool_connection_manager(self, server_id, legacy_manager):
        """Return the provider-owned manager for the preserved Query Tool."""
        # get_driver() has already resolved this provider and obtained the
        # manager. Do not repeat the lookup: the legacy call owns exception
        # timing and some manager registries expose stateful retrieval.
        if not isinstance(server_id, int):
            raise PostgreSQLProviderError(
                'PostgreSQL server identity must be an integer'
            )
        return legacy_manager

    @staticmethod
    def query_tool_execute_async(connection, source, server_cursor=False):
        """Preserve psycopg3 asynchronous Query Tool execution."""
        return connection.execute_async(
            source, server_cursor=server_cursor
        )

    @staticmethod
    def query_tool_poll(connection, **kwargs):
        """Preserve psycopg3 Query Tool result polling."""
        return connection.poll(**kwargs)

    @staticmethod
    def query_tool_fetch(connection, *args, **kwargs):
        """Preserve psycopg3 Query Tool result-window fetching."""
        return connection.async_fetchmany_2darray(*args, **kwargs)

    def validate_endpoint(self, request):
        payload = _mapping(request)
        try:
            route = payload.get('route', {})
            _required_int(route, 'server_id')
        except PostgreSQLProviderError as exc:
            return self._diagnostic(
                'CDE_PG_ENDPOINT_INVALID', str(exc), severity='error'
            )
        return self._diagnostic(
            'CDE_PG_ENDPOINT_VALID',
            'PostgreSQL endpoint is structurally valid',
        )

    def discover_endpoint(self, request):
        payload = copy.deepcopy(_mapping(request))
        route = payload.get('route', {})
        server_id = _required_int(route, 'server_id')
        manager = self._driver.connection_manager(server_id)
        if manager is None:
            raise PostgreSQLProviderError('PostgreSQL server is unavailable')
        runtime_version = str(manager.version)
        driver_version = str(self._driver.version())
        if runtime_version != PROFILE_VERSION:
            raise PostgreSQLProviderError(
                'PostgreSQL runtime does not match exact profile 18.3'
            )
        if driver_version not in APPROVED_DRIVER_VERSIONS:
            raise PostgreSQLProviderError(
                'psycopg runtime is outside the approved exact versions'
            )
        payload['identity'] = self._identity()
        payload['verified_runtime'] = {
            'engine': 'PostgreSQL',
            'server_version': runtime_version,
            'driver': LEGACY_DRIVER_TYPE,
            'driver_version': driver_version,
            'verification_state': 'verified',
        }
        return payload

    def list_resources(self, request):
        self.permissions.require('data_read', 'resource')
        payload = _mapping(request)
        extension = _extension(payload)
        catalog_kind = extension.get('child_kind')
        rows = self._catalog.read(catalog_kind, extension)
        return [
            self._resource(payload, extension, catalog_kind, row)
            for row in rows
        ]

    def inspect_resource(self, request):
        self.permissions.require('data_read', 'resource')
        payload = _mapping(request)
        extension = _extension(payload)
        catalog_kind = extension.get('catalog_kind')
        object_id = _required_int(extension, 'object_id')
        rows = self._catalog.read(catalog_kind, extension, object_id)
        if len(rows) != 1:
            raise PostgreSQLProviderError(
                'PostgreSQL resource identity did not resolve exactly once'
            )
        return self._resource(payload, extension, catalog_kind, rows[0])

    def _resource(self, parent, route, catalog_kind, row):
        id_field = 'did' if catalog_kind == 'database' else 'oid'
        object_id = row.get(id_field)
        name = row.get('name')
        if object_id is None or not isinstance(name, str):
            raise PostgreSQLProviderError(
                'PostgreSQL catalog row lacks provider authority identity'
            )
        authority_path = list(parent.get('authority_path', []))
        authority_path.extend((catalog_kind, str(object_id)))
        display_path = list(parent.get('display_path', []))
        display_path.append(name)
        resource_id = 'postgresql:' + '/'.join(authority_path)
        resource_route = {
            key: route[key]
            for key in ('server_id', 'database_id', 'schema_id')
            if key in route
        }
        resource_route.update({
            'catalog_kind': catalog_kind,
            'object_id': object_id,
            'legacy_metadata': dict(row),
        })
        return {
            'identity': self._identity(),
            'endpoint_id': self.context.endpoint_id,
            'resource_id': resource_id,
            'identity_kind': 'postgresql-oid',
            'resource_kind': catalog_kind,
            'model_family': 'relational',
            'display_name': name,
            'parent_resource_id': parent.get('resource_id'),
            'display_path': display_path,
            'authority_path': authority_path,
            'is_virtual': False,
            'generation': parent.get(
                'generation', self.context.cache_namespace
            ),
            'capability_ids': ['postgresql.catalog.read'],
            'extensions': {'postgresql': resource_route},
        }

    def open_session(self, request):
        self.permissions.require('network')
        payload = _mapping(request)
        route = payload.get('route', {})
        server_id = _required_int(route, 'server_id')
        database_id = route.get('database_id')
        if database_id is not None:
            database_id = _required_int(route, 'database_id')
        principal = route.get('principal_reference', '')
        if principal.startswith('worker:'):
            owner_id = _required_int(route, 'owner_id')
            manager = self._driver.delegated_connection_manager(
                server_id, owner_id,
                route.get('source_kind', 'server'),
            )
        else:
            manager = self._driver.connection_manager(server_id)
        connection_id = str(route.get('connection_id') or uuid.uuid4())
        connection = manager.connection(
            did=database_id, conn_id=connection_id
        )
        self._connect_delegated_route(connection, manager, route)
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = _SessionState(
            server_id, database_id, connection_id, connection
        )
        return {
            'identity': self._identity(),
            'session_id': session_id,
            'endpoint_id': self.context.endpoint_id,
            'route_id': str(route.get('route_id', 'legacy-pgadmin-server')),
            'principal_reference': str(
                route.get('principal_reference', 'pgadmin-session')
            ),
            'language_profile': 'postgresql-sql',
            'transaction_model': 'postgresql-native',
            'provider_state': {
                'server_id': server_id,
                'database_id': database_id,
                'connection_id': connection_id,
            },
            'occurrence_id': self.context.session_namespace,
            'limits': {},
            'extensions': {'postgresql': {'provider_owned': True}},
        }

    def _connect_delegated_route(self, connection, manager, route):
        """Connect a worker route without synthesizing an interactive user."""
        references = dict(route.get('credential_references', {}))
        primary = route.get('credential_reference_id')
        primary_kind = route.get('credential_kind', 'database_password')
        if primary is not None:
            references.setdefault(primary_kind, primary)
        if not references:
            return
        unsupported = set(references).difference({
            'database_password', 'tunnel_password',
        })
        if unsupported:
            raise PostgreSQLProviderError(
                'PostgreSQL delegated credential kind is unsupported'
            )
        principal = route.get('principal_reference')
        if not isinstance(principal, str) or not principal.startswith(
                'worker:'):
            raise PostgreSQLProviderError(
                'PostgreSQL delegated route principal is invalid'
            )
        values = {}
        with ExitStack() as stack:
            for kind, reference_id in references.items():
                lease = stack.enter_context(self.permissions.acquire_secret(
                    reference_id, principal, 'connect', kind
                ))
                values[kind] = lease.use(
                    lambda value: bytes(value).decode('utf-8')
                )
            status, error = connection.connect(
                user=manager.user,
                password=values.get('database_password'),
                tunnel_password=values.get('tunnel_password', ''),
            )
            values.clear()
        if not status:
            raise PostgreSQLProviderError(
                'PostgreSQL delegated connection failed: ' + str(error)
            )

    def describe_transaction(self, request):
        payload = _mapping(request)
        session_id = payload.get('session_id')
        state = self._sessions.get(session_id)
        if state is None:
            raise PostgreSQLProviderError('PostgreSQL session is unavailable')
        return {
            'identity': self._identity(),
            'session_id': session_id,
            'transaction_model': 'postgresql-native',
            'provider_payload': {
                'transaction_status': state.connection.transaction_status(),
                'interpretation': 'PostgreSQL provider-owned opaque value',
            },
            'authority_reference': 'provider:org.pgadmin.postgresql',
            'extensions': {},
        }

    def control_transaction(self, request):
        """Execute native PostgreSQL transaction control on this session."""
        payload = _mapping(request)
        state = self._sessions.get(payload.get('session_id'))
        if state is None:
            raise PostgreSQLProviderError('PostgreSQL session is unavailable')
        action = payload.get('action')
        statements = {'commit': 'COMMIT;', 'rollback': 'ROLLBACK;'}
        if action not in statements:
            raise PostgreSQLProviderError(
                'PostgreSQL transaction action is unavailable'
            )
        status, _detail = state.connection.execute_void(statements[action])
        if not status:
            raise PostgreSQLProviderError(
                'PostgreSQL transaction action outcome is provider-owned'
            )
        return self.describe_transaction({
            'session_id': payload.get('session_id'),
        })

    def complete(self, request):
        """Use pgAdmin's existing PostgreSQL completion implementation."""
        payload = _mapping(request)
        session_id = payload.get('session_id')
        state = self._sessions.get(session_id)
        if state is None:
            raise PostgreSQLProviderError('PostgreSQL session is unavailable')
        adapter = self._completion_adapters.get(session_id)
        if adapter is None:
            # Import lazily so provider registration does not make the legacy
            # Query Tool completion dependency part of common app startup.
            from pgadmin.utils.sqlautocomplete.autocomplete import (
                SQLAutoComplete,
            )
            adapter = SQLAutoComplete(
                sid=state.server_id,
                did=state.database_id,
                conn=state.connection,
            )
            self._completion_adapters[session_id] = adapter
        return adapter.get_completions(
            str(payload.get('full_source', '')),
            str(payload.get('source_before_cursor', '')),
        )

    def execute(self, request):
        self.permissions.require('execute')
        payload = _mapping(request)
        session_id = payload.get('session_id')
        state = self._sessions.get(session_id)
        if state is None:
            raise PostgreSQLProviderError('PostgreSQL session is unavailable')
        status, detail = state.connection.execute_async(
            payload.get('source', ''), payload.get('parameters') or None
        )
        operation_id = str(uuid.uuid4())
        operation = {
            'identity': self._identity(),
            'operation_id': operation_id,
            'operation_kind': 'postgresql-query',
            'target_resource_id': None,
            'capability_id': 'postgresql.query.execute',
            'risk_class': 'unknown',
            'provider_state': {
                'session_id': session_id,
                'accepted': bool(status),
            },
            'terminal': not bool(status),
            'provider_receipt': None if status else {'error': str(detail)},
            'extensions': {'postgresql': {'provider_owned': True}},
        }
        self._operations[operation_id] = _OperationState(
            operation, payload.get('execution_id', ''), session_id,
            state.connection,
        )
        return copy.deepcopy(operation)

    def cancel(self, request):
        self.permissions.require('execute')
        payload = _mapping(request)
        state = self._operation(payload.get('operation_id'))
        session = self._sessions[state.session_id]
        status, detail = state.connection.cancel_transaction(
            session.connection_id, session.database_id
        )
        state.operation['provider_receipt'] = {
            'cancel_request_accepted': bool(status),
            'detail': str(detail),
            'outcome': 'pending-provider-observation',
        }
        return copy.deepcopy(state.operation)

    def describe_result(self, request):
        payload = _mapping(request)
        state = self._operation(payload.get('operation_id'))
        if state.result is not None:
            return copy.deepcopy(state.result)
        poll_status, detail = state.connection.poll(
            formatted_exception_msg=True, no_result=True
        )
        complete = poll_status == 1
        rows = None
        if complete:
            row_status, rows = state.connection.async_fetchmany_2darray(-1)
            if not row_status:
                detail = rows
                rows = None
                complete = False
        result = {
            'identity': self._identity(),
            'result_id': str(uuid.uuid4()),
            'execution_id': state.execution_id,
            'result_kind': 'tabular',
            'schema': {
                'columns': state.connection.get_column_info() or [],
            },
            'stream_reference': None,
            'complete': complete,
            'continuation': (
                None if complete else state.operation['operation_id']
            ),
            'extensions': {
                'postgresql': {
                    'rows': rows,
                    'poll_status': poll_status,
                    'detail': detail,
                    'provider_owned': True,
                }
            },
        }
        if complete or poll_status is False:
            state.result = copy.deepcopy(result)
            state.operation['terminal'] = True
        return result

    def select_renderer(self, request):
        """Select the preserved pgAdmin grid for PostgreSQL tabular rows."""
        payload = _mapping(request)
        if payload.get('result_kind') != 'tabular':
            raise PostgreSQLProviderError(
                'PostgreSQL provider has no renderer for this result kind'
            )
        return {
            'identity': self._identity(),
            'endpoint_id': self.context.endpoint_id,
            'resource_id': 'postgresql:renderer:legacy-data-grid',
            'identity_kind': 'provider-renderer-id',
            'resource_kind': 'result-renderer',
            'model_family': 'relational',
            'display_name': 'PostgreSQL tabular data grid',
            'parent_resource_id': None,
            'display_path': ['PostgreSQL', 'Result renderers', 'Data grid'],
            'authority_path': [
                'postgresql', 'result-renderer', 'legacy-data-grid'
            ],
            'is_virtual': True,
            'generation': self.context.cache_namespace,
            'capability_ids': [TABULAR_RENDER_CAPABILITY],
            'extensions': {
                'postgresql': {
                    'component_reference': 'SchemaView/DataGridView',
                    'provider_owned': True,
                },
            },
        }

    def translate_diagnostic(self, request):
        payload = _mapping(request)
        return self._diagnostic(
            str(payload.get('code', 'CDE_PG_DIAGNOSTIC')),
            str(payload.get('message', 'PostgreSQL provider diagnostic')),
            severity=str(payload.get('severity', 'unknown')),
            retryable=bool(payload.get('retryable', False)),
            details=payload.get('details', {}),
        )

    def get_operation(self, request):
        payload = _mapping(request)
        return copy.deepcopy(
            self._operation(payload.get('operation_id')).operation
        )

    def list_tools(self, request):
        _mapping(request)
        return [
            {
                'identity': self._identity(),
                'endpoint_id': self.context.endpoint_id,
                'resource_id': f'postgresql-tool:{tool_id}',
                'identity_kind': 'provider-tool-id',
                'resource_kind': 'tool',
                'model_family': 'relational',
                'display_name': tool_id,
                'parent_resource_id': None,
                'display_path': [tool_id],
                'authority_path': ['postgresql', 'tool', tool_id],
                'is_virtual': True,
                'generation': self.context.cache_namespace,
                'capability_ids': [],
                'extensions': {
                    'postgresql': {
                        'disposition': disposition,
                        'ownership': 'provider-local',
                        'common_service': False,
                    }
                },
            }
            for tool_id, disposition in TOOL_DISPOSITIONS.items()
        ]

    def _operation(self, operation_id):
        try:
            return self._operations[operation_id]
        except KeyError as exc:
            raise PostgreSQLProviderError(
                'PostgreSQL operation is unavailable'
            ) from exc

    def close(self):
        """Drop handles; the legacy driver retains connection GC authority."""
        self._sessions.clear()
        self._operations.clear()
        self._completion_adapters.clear()
        self._closed = True


def create_provider(context, permissions):
    """Create one endpoint-isolated PostgreSQL provider instance."""
    return PostgreSQLProvider(context, permissions)
